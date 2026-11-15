import asyncio
import base64
import io
import json
import os
import struct
import unittest
import wave
from unittest.mock import MagicMock, patch

import httpx
from pymongo.errors import DuplicateKeyError

from paperflux.src.agents import gemini_client as gc
from paperflux.src.agents.gemini_client import GeminiError
from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError
from paperflux.src.agents.paper_podcast.agent import PodcastWriterAgent, build_user_text
from paperflux.src.config.settings import rpm_for
from paperflux.src.podcast import audio
from paperflux.src.podcast.script import parse_script, script_schema, speakable, tts_prompt
from paperflux.src.services import database
from paperflux.src.services import paper_processor as pp
from paperflux.src.services.podcast_producer import FAILED, GAVE_UP, READY, SCRIPTED, PodcastProducer
from tests.test_llm_clients import make_client, quota

HOSTS = ({"name": "Maya", "gender": "female", "voice": "Vindemiatrix"},
         {"name": "Theo", "gender": "male", "voice": "Algieba"})
SENTENCE = "This calm line explains one part of the paper in plain words for a listener today."  # 16 words


def script_json(turns=20, words_per_turn=2, speakers=("Maya", "Theo")):
    body = [{"speaker": speakers[i % len(speakers)], "text": " ".join([SENTENCE] * words_per_turn)} for i in range(turns)]
    return json.dumps({"title": "Why small adapters work", "summary": "A short talk.", "turns": body})


def pcm_seconds(seconds, rate=24000):
    return struct.pack("<h", 1000) * int(rate * seconds)


# ------------------------------------------------------------------ script --
class ScriptTest(unittest.TestCase):
    def test_good_script_parses_and_estimates_length(self):
        script, problems = parse_script(script_json(), HOSTS, 700)
        self.assertEqual(problems, [])
        self.assertEqual(len(script["turns"]), 20)
        self.assertEqual(script["words"], 640)
        self.assertAlmostEqual(script["minutes"], 4.6, places=1)

    def test_speakers_are_normalised_and_repeats_merged(self):
        raw = json.dumps({"title": "t", "turns": [
            {"speaker": "maya", "text": SENTENCE}, {"speaker": "Host B", "text": SENTENCE},
            {"speaker": "male", "text": SENTENCE}, {"speaker": "Narrator", "text": SENTENCE},
        ] + [{"speaker": ("Maya", "Theo")[i % 2], "text": SENTENCE * 2} for i in range(12)]})
        script, problems = parse_script(raw, HOSTS, 700)
        self.assertEqual([t["speaker"] for t in script["turns"][:3]], ["Maya", "Theo", "Maya"])
        self.assertIn(SENTENCE + " " + SENTENCE, script["turns"][1]["text"])  # Host B + male merged
        self.assertTrue(any("Narrator" in p for p in problems))

    def test_text_is_made_speakable(self):
        said = speakable("**Really** [laughs] see https://x.io: $\\alpha$ is ~5% of the 4096 x 8 matrix, "
                         "e.g. A → B (pauses) and W ≥ 2 & `code`")
        for gone in ("**", "[", "http", "$", "\\", "`", "→", "≥", "pauses"):
            self.assertNotIn(gone, said)
        for kept in ("Really", "alpha", "about 5%", "4096 by 8", "for example", "to B", "at least 2", "and"):
            self.assertIn(kept, said)

    def test_unusable_scripts_are_rejected_with_reasons(self):
        self.assertIsNone(parse_script("not json", HOSTS, 700)[0])
        one_voice, problems = parse_script(script_json(speakers=("Maya",)), HOSTS, 700)
        self.assertIsNone(one_voice)
        self.assertTrue(any("Both hosts" in p for p in problems))
        short, problems = parse_script(script_json(turns=12, words_per_turn=1), HOSTS, 700)
        self.assertIsNone(short)
        self.assertTrue(any("words" in p for p in problems))

    def test_long_scripts_are_cut_to_budget_but_keep_the_sign_off(self):
        raw = json.loads(script_json(turns=40, words_per_turn=3))
        raw["turns"][-1]["text"] = "Thanks for listening, goodbye."
        script, problems = parse_script(json.dumps(raw), HOSTS, 700)
        self.assertLessEqual(script["words"], 875)
        self.assertEqual(script["turns"][-1]["text"], "Thanks for listening, goodbye.")
        speakers = [t["speaker"] for t in script["turns"]]
        self.assertTrue(all(a != b for a, b in zip(speakers, speakers[1:])))
        self.assertTrue(any("shortened" in p for p in problems))

    def test_tts_prompt_directs_a_calm_delivery_and_names_both_hosts(self):
        script, _ = parse_script(script_json(), HOSTS, 700)
        prompt = tts_prompt(script, HOSTS)
        self.assertIn("calm", prompt)
        self.assertIn("\nMaya: ", prompt)
        self.assertIn("\nTheo: ", prompt)
        self.assertEqual(script_schema(HOSTS)["properties"]["turns"]["items"]["properties"]["speaker"]["enum"], ["Maya", "Theo"])


# ------------------------------------------------------------------ writer --
PAPER = {"paper_id": "2611.1", "title": "LoRA", "summary": "abstract", "explanation": "## How It Works\nsteps",
         "visual_brief": "### Results tables\n- LoRA: MNLI = 87.5",
         "insights": {"tldr": "Tiny adapters.", "key_numbers": [{"label": "Params", "value": "0.01%", "context": "of FT"}],
                      "core_concept": {"name": "Low rank", "explanation": "BA", "analogy": "sticky note"},
                      "insights": [{"title": "Matches FT", "summary": "s", "why_it_matters": "w", "evidence": "Table 2"}]}}


class WriterTest(unittest.TestCase):
    def test_writer_is_grounded_in_all_notes_and_uses_the_hosts(self):
        client = MagicMock()
        client.generate_text.return_value = script_json()
        script = PodcastWriterAgent(client=client, hosts=HOSTS, target_words=650).run(PAPER)
        self.assertEqual(script["title"], "Why small adapters work")
        kwargs = client.generate_text.call_args.kwargs
        for needle in ("0.01%", "MNLI = 87.5", "sticky note", "How It Works", "Matches FT"):
            self.assertIn(needle, kwargs["user_text"])
        self.assertIn("**Maya**", kwargs["system_instruction"])
        self.assertIn("about 650 words", kwargs["system_instruction"])
        self.assertNotIn("{{", kwargs["system_instruction"])
        self.assertEqual(kwargs["response_schema"]["properties"]["turns"]["items"]["properties"]["speaker"]["enum"],
                         ["Maya", "Theo"])

    def test_unusable_script_gets_one_repair_then_fails_loudly(self):
        client = MagicMock()
        client.generate_text.side_effect = [script_json(speakers=("Maya",)), script_json()]
        self.assertTrue(PodcastWriterAgent(client=client, hosts=HOSTS).run(PAPER)["turns"])
        self.assertIn("could not be recorded", client.generate_text.call_args.kwargs["user_text"])
        client.generate_text.side_effect = ["{}", "{}"]
        with self.assertRaises(LLMError):
            PodcastWriterAgent(client=client, hosts=HOSTS).run(PAPER)

    def test_quota_propagates(self):
        client = MagicMock()
        client.generate_text.side_effect = QuotaExhaustedError("spent")
        with self.assertRaises(QuotaExhaustedError):
            PodcastWriterAgent(client=client, hosts=HOSTS).run(PAPER)

    def test_missing_pack_still_builds_a_prompt(self):
        text = build_user_text({"paper_id": "1", "title": "T", "explanation": "body"})
        self.assertIn("body", text)
        self.assertNotIn("Explainer notes", text)


# ------------------------------------------------------------------ TTS -----
def audio_response(chunks=(b"\x01\x00" * 10,), mime="audio/L16;codec=pcm;rate=24000"):
    parts = [{"inlineData": {"mimeType": mime, "data": base64.b64encode(c).decode()}} for c in chunks]
    return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]})


class SpeechClientTest(unittest.TestCase):
    def setUp(self):
        gc.reset_quota_state()

    def test_multi_speaker_request_and_pcm_response(self):
        seen = {}

        def handler(request):
            seen["url"], seen["body"] = str(request.url), json.loads(request.content)
            return audio_response(chunks=(b"\x01\x00" * 4, b"\x02\x00" * 4), mime="audio/L16;codec=pcm;rate=22050")

        client, _ = make_client(handler, models=("gemini-3.8-flash-tts",))
        pcm, rate = client.generate_speech("Maya: hi\nTheo: hello", [("Maya", "Vindemiatrix"), ("Theo", "Algieba")])
        self.assertEqual((pcm, rate), (b"\x01\x00" * 4 + b"\x02\x00" * 4, 22050))
        config = seen["body"]["generationConfig"]
        self.assertEqual(config["responseModalities"], ["AUDIO"])
        voices = config["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]
        self.assertEqual([(v["speaker"], v["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]) for v in voices],
                         [("Maya", "Vindemiatrix"), ("Theo", "Algieba")])
        self.assertNotIn("systemInstruction", seen["body"])
        self.assertIn("gemini-3.8-flash-tts:generateContent", seen["url"])

    def test_unknown_tts_model_id_falls_through_to_the_next(self):
        def handler(request):
            if "preview" not in str(request.url):
                return httpx.Response(404, text="models/gemini-3.8-flash-tts is not found")
            return audio_response()

        client, _ = make_client(handler, models=("gemini-3.8-flash-tts", "gemini-3.8-flash-preview-tts"))
        self.assertEqual(client.generate_speech("x", [("A", "v"), ("B", "w")])[1], 24000)

    def test_no_audio_and_daily_quota_are_reported(self):
        client, _ = make_client(lambda r: httpx.Response(200, json={"candidates": [{"finishReason": "OTHER"}]}),
                                models=("tts-a",))
        with self.assertRaises(GeminiError):
            client.generate_speech("x", [("A", "v"), ("B", "w")])
        client, _ = make_client(lambda r: quota(daily=True), models=("tts-b",))
        with self.assertRaises(QuotaExhaustedError):
            client.generate_speech("x", [("A", "v"), ("B", "w")])

    def test_tts_models_get_the_free_tier_rpm(self):
        self.assertEqual(rpm_for("gemini-3.8-flash-tts"), 3)


# ------------------------------------------------------------------ audio ---
class AudioTest(unittest.TestCase):
    def test_mp3_is_far_smaller_than_pcm(self):
        pcm = pcm_seconds(3)
        data, mime = audio.encode(pcm, 24000)
        self.assertEqual(mime, "audio/mpeg")
        self.assertTrue(data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0))
        self.assertLess(len(data), len(pcm) / 4)
        self.assertAlmostEqual(audio.duration_seconds(pcm, 24000), 3.0)

    def test_wav_fallback_when_the_encoder_is_missing(self):
        saved, audio.lameenc = audio.lameenc, None
        try:
            data, mime = audio.encode(pcm_seconds(1) + b"\x00", 24000)  # odd byte is dropped
        finally:
            audio.lameenc = saved
        self.assertEqual(mime, "audio/wav")
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (24000, 1, 2))
            self.assertAlmostEqual(w.getnframes() / 24000, 1 + 2 * audio.LEAD_IN_SECONDS, places=2)


# ------------------------------------------------------------------ producer -
class FakePodcastDB:
    def __init__(self, docs, used=0):
        self.docs = {d["paper_id"]: d for d in docs}
        self.used = used
        self.audio = {}
        self.collection = MagicMock()
        self.collection.find_one.side_effect = lambda q: self.docs.get(q["paper_id"])

    def set_podcast(self, pid, podcast):
        self.docs[pid] = {**self.docs[pid], "podcast": dict(podcast)}

    def save_podcast_audio(self, pid, data, mime):
        self.audio[pid] = (data, mime)
        return f"file-{pid}"

    def tts_requests_today(self):
        return self.used

    def reserve_tts_request(self, limit):
        if self.used >= limit:
            return False
        self.used += 1
        return True


class FakeWriter:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def run(self, doc):
        self.calls.append(doc["paper_id"])
        if self.error:
            raise self.error
        return parse_script(script_json(), HOSTS, 700)[0]


class FakeTTS:
    def __init__(self, errors=(), seconds=260):
        self.calls, self.errors, self.seconds = [], list(errors), seconds

    def generate_speech(self, prompt, speakers):
        self.calls.append((prompt, list(speakers)))
        if self.errors:
            raise self.errors.pop(0)
        return pcm_seconds(self.seconds, 8000), 8000


def doc(pid, **extra):
    return {"paper_id": pid, "title": pid, "explanation": "analysis", **extra}


def producer(db, writer=None, tts=None, per_day=10):
    return PodcastProducer(db, writer=writer or FakeWriter(), tts=tts or FakeTTS(), hosts=HOSTS, per_day=per_day)


class ProducerTest(unittest.TestCase):
    def test_happy_path_records_in_priority_order(self):
        db = FakePodcastDB([doc("a"), doc("b")])
        tts = FakeTTS()
        progress = []
        counts = producer(db, tts=tts).run(["b", "a"], on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(counts[READY], 2)
        pod = db.docs["b"]["podcast"]
        self.assertEqual((pod["status"], pod["audio_id"], pod["mime"]), (READY, "file-b", "audio/mpeg"))
        self.assertAlmostEqual(pod["duration_s"], 260, delta=1)
        self.assertEqual([h["voice"] for h in pod["hosts"]], ["Vindemiatrix", "Algieba"])
        self.assertEqual(tts.calls[0][1], [("Maya", "Vindemiatrix"), ("Theo", "Algieba")])
        self.assertIn("Maya: ", tts.calls[0][0])
        self.assertEqual(progress[-1], (2, 2))

    def test_daily_budget_is_respected_and_no_scripts_are_wasted(self):
        db = FakePodcastDB([doc("a"), doc("b"), doc("c")], used=8)
        writer, tts = FakeWriter(), FakeTTS()
        counts = producer(db, writer=writer, tts=tts, per_day=10).run(["a", "b", "c"])
        self.assertEqual((counts[READY], counts["deferred"]), (2, 1))
        self.assertEqual(len(tts.calls), 2)
        self.assertEqual(writer.calls, ["a", "b"])  # c is not scripted: nobody could record it today
        self.assertNotIn("podcast", db.docs["c"])

    def test_tts_quota_keeps_the_script_and_the_next_run_reuses_it(self):
        db = FakePodcastDB([doc("a")])
        writer = FakeWriter()
        counts = producer(db, writer=writer, tts=FakeTTS(errors=[QuotaExhaustedError("spent")])).run(["a"])
        self.assertEqual(counts["deferred"], 1)
        self.assertEqual(db.docs["a"]["podcast"]["status"], SCRIPTED)
        producer(db, writer=writer).run(["a"])
        self.assertEqual(writer.calls, ["a"])  # the script was paid for once
        self.assertEqual(db.docs["a"]["podcast"]["status"], READY)

    def test_failures_are_retried_once_then_given_up(self):
        db = FakePodcastDB([doc("a")])
        producer(db, tts=FakeTTS(errors=[GeminiError("boom")])).run(["a"])
        self.assertEqual((db.docs["a"]["podcast"]["status"], db.docs["a"]["podcast"]["attempts"]), (FAILED, 1))
        producer(db, tts=FakeTTS(errors=[GeminiError("boom again")])).run(["a"])
        self.assertEqual(db.docs["a"]["podcast"]["status"], GAVE_UP)
        tts = FakeTTS()
        producer(db, tts=tts).run(["a"])
        self.assertEqual(tts.calls, [])  # given up: never spends budget again

    def test_cut_off_audio_is_a_failure_not_an_episode(self):
        db = FakePodcastDB([doc("a")])
        counts = producer(db, tts=FakeTTS(seconds=20)).run(["a"])
        self.assertEqual(counts[FAILED], 1)
        self.assertIn("cut off", db.docs["a"]["podcast"]["error"])
        self.assertEqual(db.audio, {})

    def test_skips_ready_and_unanalysed_papers_and_stops_on_writer_quota(self):
        db = FakePodcastDB([doc("ready", podcast={"status": READY}), {"paper_id": "raw"}, doc("a"), doc("b")])
        writer = FakeWriter(error=QuotaExhaustedError("spent"))
        counts = producer(db, writer=writer).run(["ready", "raw", "missing", "a", "b"])
        self.assertEqual(writer.calls, ["a"])
        self.assertEqual(counts["deferred"], 2)


# ------------------------------------------------------------------ database -
class DatabasePodcastTest(unittest.TestCase):
    def make(self):
        db = database.DatabaseService.__new__(database.DatabaseService)
        db.collection, db.metadata_collection = MagicMock(), MagicMock()
        db._cache, db._cache_timestamp = {}, 0
        db._cache_lock = __import__("threading").Lock()
        db._audio_bucket = MagicMock()
        return db

    def test_tts_reservation_is_atomic_per_quota_day(self):
        db = self.make()
        self.assertTrue(db.reserve_tts_request(10))
        query, update = db.metadata_collection.find_one_and_update.call_args.args
        self.assertTrue(query["_id"].startswith("tts_usage_20"))
        self.assertEqual(query["count"], {"$lt": 10})
        self.assertEqual(update, {"$inc": {"count": 1}})
        db.metadata_collection.find_one_and_update.side_effect = DuplicateKeyError("full")
        self.assertFalse(db.reserve_tts_request(10))

    def test_upsert_preserves_fields_from_later_stages(self):
        db = self.make()
        paper = MagicMock(paper_id="a")
        paper.to_dict.return_value = {"paper_id": "a", "explanation": "x"}
        db.upsert_paper(paper)
        self.assertEqual(db.collection.update_one.call_args.args[1], {"$set": {"paper_id": "a", "explanation": "x"}})
        db.collection.replace_one.assert_not_called()

    def test_pruning_papers_also_prunes_their_audio(self):
        db = self.make()
        db.collection.delete_many.return_value.deleted_count = 1
        db._audio_bucket.find.return_value = [MagicMock(_id="old")]
        db.delete_papers_not_in(["keep"])
        db._audio_bucket.find.assert_called_with({"metadata.paper_id": {"$nin": ["keep"]}})
        db._audio_bucket.delete.assert_called_with("old")

    def test_bad_audio_ids_return_none(self):
        self.assertIsNone(self.make().get_podcast_audio("not-an-object-id"))


# ------------------------------------------------------------------ processor -
class ProcessorPodcastStageTest(unittest.TestCase):
    def test_podcasts_run_after_the_papers_in_upvote_order_and_cannot_break_the_run(self):
        from tests.test_processor import FakeAnalyst, FakeDB, FakeFetcher, FakeInsights, entry, make_processor

        class Recorder:
            def __init__(self, error=None):
                self.calls, self.error = [], error

            def run(self, ids, on_progress=None):
                self.calls.append(list(ids))
                if self.error:
                    raise self.error
                return {}

        db = FakeDB()
        recorder = Recorder()
        proc = make_processor(db, FakeFetcher([entry("a", 1), entry("b", 9)]), FakeAnalyst(), FakeInsights())
        proc._podcast_producer = recorder
        self.assertTrue(asyncio.run(proc.process_papers()))
        self.assertEqual(recorder.calls, [["b", "a"]])
        self.assertTrue(db.has_complete_paper("a"))

        proc = make_processor(FakeDB(), FakeFetcher([entry("c")]), FakeAnalyst(), FakeInsights())
        proc._podcast_producer = Recorder(error=RuntimeError("tts down"))
        self.assertTrue(asyncio.run(proc.process_papers()))

    def test_podcasts_never_borrow_the_paper_agents_keys(self):
        from paperflux.src.config import settings

        clean = {k: v for k, v in os.environ.items() if not k.startswith("GEMINI_")}
        with patch.dict(os.environ, {**clean, "GEMINI_INSIGHTS_API_KEY": "ins", "GEMINI_API_KEY": "legacy"}, clear=True):
            self.assertEqual((settings.load_podcast_keys(), settings.load_tts_keys()), ([], []))
        with patch.dict(os.environ, {**clean, "GEMINI_PODCAST_API_KEY": "pod"}, clear=True):
            self.assertEqual(settings.load_tts_keys(), ["pod"])  # one new-project key covers TTS too
        with patch.dict(os.environ, {**clean, "GEMINI_PODCAST_API_KEY": "pod", "GEMINI_TTS_API_KEY": "tts"}, clear=True):
            self.assertEqual(settings.load_tts_keys(), ["tts"])

    def test_reusing_a_paper_agent_key_for_podcasts_is_called_out(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GEMINI_")}
        proc = pp.PaperProcessor.__new__(pp.PaperProcessor)
        proc._podcast_producer, proc.db = None, MagicMock()
        env = {**clean, "GEMINI_INSIGHTS_API_KEY": "same", "GEMINI_PODCAST_API_KEY": "same"}
        with patch.dict(os.environ, env, clear=True), self.assertLogs("paperflux.paper_processor", "WARNING") as logs:
            self.assertIsNotNone(proc.podcast_producer)
        self.assertIn("NEW project", " ".join(logs.output))

    def test_no_keys_means_no_podcast_stage(self):
        proc = pp.PaperProcessor.__new__(pp.PaperProcessor)
        proc._podcast_producer = None
        saved = pp.load_podcast_keys
        pp.load_podcast_keys = lambda: []
        try:
            self.assertIsNone(proc.podcast_producer)
        finally:
            pp.load_podcast_keys = saved


if __name__ == "__main__":
    unittest.main()
