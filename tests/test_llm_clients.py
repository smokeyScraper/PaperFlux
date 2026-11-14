import json
import tempfile
import unittest

import httpx

from paperflux.src.agents import gemini_client as gc
from paperflux.src.agents.fallback import FallbackLLM
from paperflux.src.agents.gemini_client import GeminiClient, parse_rate_limit
from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError
from paperflux.src.agents.openai_compat_client import OpenAICompatClient

SCHEMA = {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}}


def ok(text="{\"a\": \"x\"}", finish="STOP"):
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}
    )


def quota(daily: bool, retry="34s"):
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry},
            ],
        }
    }
    return httpx.Response(429, json=body)


class FakeTime:
    def __init__(self):
        self.now = 1_000_000.0
        self.slept: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make_client(handler, models=("gemini-3.8-flash", "gemini-3.5-flash-lite"), keys=("key-A",), **kw):
    t = FakeTime()
    client = GeminiClient(
        api_keys=list(keys),
        models=list(models),
        transport=httpx.MockTransport(handler),
        sleep=t.sleep,
        clock=t.clock,
        **kw,
    )
    return client, t


class GeminiClientTest(unittest.TestCase):
    def setUp(self):
        gc.reset_quota_state()

    def test_sends_schema_and_keeps_key_out_of_url(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["headers"] = request.headers
            seen["body"] = json.loads(request.content)
            return ok()

        client, _ = make_client(handler)
        self.assertEqual(client.generate_text("hi", "sys", response_schema=SCHEMA), '{"a": "x"}')
        self.assertNotIn("key-A", seen["url"])
        self.assertEqual(seen["headers"]["x-goog-api-key"], "key-A")
        cfg = seen["body"]["generationConfig"]
        self.assertEqual(cfg["responseMimeType"], "application/json")
        self.assertEqual(cfg["responseSchema"], SCHEMA)
        self.assertIn("gemini-3.8-flash:generateContent", seen["url"])
        self.assertEqual(seen["body"]["systemInstruction"]["parts"][0]["text"], "sys")

    def test_gemma_gets_prompt_hint_not_schema_and_folds_system(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return ok()

        client, _ = make_client(handler, models=["gemma-4-31b-it"])
        client.generate_text("hi", "SYSTEM-RULES", response_schema=SCHEMA)
        body = seen["body"]
        self.assertNotIn("responseSchema", body["generationConfig"])
        self.assertNotIn("systemInstruction", body)
        first_text = body["contents"][0]["parts"][0]["text"]
        self.assertIn("SYSTEM-RULES", first_text)
        self.assertIn("JSON schema", first_text)

    def test_daily_quota_falls_back_and_is_remembered(self):
        calls = []

        def handler(request):
            model = str(request.url).split("/models/")[1].split(":")[0]
            calls.append(model)
            return quota(daily=True) if model == "gemini-3.8-flash" else ok("fine")

        client, t = make_client(handler)
        self.assertEqual(client.generate_text("x"), "fine")
        self.assertEqual(calls, ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
        calls.clear()
        self.assertEqual(client.generate_text("x"), "fine")
        # exhausted bucket is skipped without spending a request
        self.assertEqual(calls, ["gemini-3.5-flash-lite"])
        # ...and becomes usable again after the Pacific-midnight reset
        t.now += 2 * 86400
        calls.clear()
        client.generate_text("x")
        self.assertEqual(calls[0], "gemini-3.8-flash")

    def test_per_minute_429_waits_retry_delay_then_succeeds(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            return quota(daily=False, retry="12s") if state["n"] == 1 else ok("done")

        client, t = make_client(handler, models=["gemini-3.8-flash"])
        self.assertEqual(client.generate_text("x"), "done")
        self.assertEqual(state["n"], 2)
        self.assertTrue(any(abs(s - 13.0) < 0.01 for s in t.slept), t.slept)

    def test_all_exhausted_raises_quota_error_then_makes_no_requests(self):
        calls = []

        def handler(request):
            calls.append(1)
            return quota(daily=True)

        client, _ = make_client(handler)
        with self.assertRaises(QuotaExhaustedError):
            client.generate_text("x")
        n = len(calls)
        with self.assertRaises(QuotaExhaustedError):
            client.generate_text("x")
        self.assertEqual(len(calls), n)  # no wasted requests

    def test_unknown_model_is_skipped_for_good(self):
        calls = []

        def handler(request):
            model = str(request.url).split("/models/")[1].split(":")[0]
            calls.append(model)
            if model == "gemini-3.5-flash":
                return httpx.Response(404, json={"error": {"message": "models/gemini-3.5-flash is not found"}})
            return ok("fine")

        client, _ = make_client(handler, models=["gemini-3.5-flash", "gemini-3.8-flash"])
        self.assertEqual(client.generate_text("x"), "fine")
        calls.clear()
        client.generate_text("x")
        self.assertEqual(calls, ["gemini-3.8-flash"])

    def test_schema_rejected_retries_same_model_without_schema(self):
        bodies = []

        def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            if "responseSchema" in body["generationConfig"]:
                return httpx.Response(400, json={"error": {"message": "schema produces a constraint that has too many states"}})
            return ok("{}")

        client, _ = make_client(handler, models=["gemini-3.8-flash"])
        self.assertEqual(client.generate_text("x", response_schema=SCHEMA), "{}")
        self.assertEqual(len(bodies), 2)
        self.assertIn("JSON schema", bodies[1]["systemInstruction"]["parts"][0]["text"])

    def test_truncated_structured_output_moves_to_next_model(self):
        def handler(request):
            model = str(request.url).split("/models/")[1].split(":")[0]
            return ok("{\"a\"", finish="MAX_TOKENS") if model == "gemini-3.8-flash" else ok("{}")

        client, _ = make_client(handler)
        self.assertEqual(client.generate_text("x", response_schema=SCHEMA), "{}")

    def test_gemma_is_skipped_for_pdf_requests(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return ok("analysis")

        client, _ = make_client(handler, models=["gemma-4-31b-it", "gemini-3.5-flash-lite"])
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(b"%PDF-1.4 test")
            f.flush()
            self.assertEqual(client.generate_from_pdf(f.name, "read"), "analysis")
        self.assertEqual(len(calls), 1)
        self.assertIn("gemini-3.5-flash-lite", calls[0])

    def test_client_side_limiter_spaces_requests(self):
        client, t = make_client(lambda r: ok("x"), models=["gemini-3.8-flash"])  # 5 RPM
        for _ in range(6):
            client.generate_text("x")
        self.assertGreaterEqual(sum(t.slept), 59.0)  # the 6th call had to wait for the window

    def test_rate_limit_parsing(self):
        self.assertTrue(parse_rate_limit('{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}').daily)
        self.assertTrue(parse_rate_limit("quota exceeded, limit: 0, model: x").daily)
        info = parse_rate_limit('{"retryDelay": "34s", "quotaId": "...PerMinute..."}')
        self.assertFalse(info.daily)
        self.assertEqual(info.retry_after, 34.0)

    def test_next_pacific_midnight_is_in_the_future_within_a_day(self):
        now = 1_731_600_000.0
        nxt = gc.next_pacific_midnight(now)
        self.assertGreater(nxt, now)
        self.assertLessEqual(nxt - now, 86400 + 10)


class OpenAICompatAndFallbackTest(unittest.TestCase):
    def setUp(self):
        gc.reset_quota_state()

    def test_openai_compat_returns_content_and_strips_think(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(request.headers["authorization"], "Bearer gk")
            self.assertEqual(body["response_format"], {"type": "json_object"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "<think>hmm</think>{\"a\": 1}"}}]})

        c = OpenAICompatClient("https://x/v1", ["gk"], "m", transport=httpx.MockTransport(handler))
        self.assertEqual(c.generate_text("hi", "sys", response_schema=SCHEMA), '{"a": 1}')

    def test_openai_compat_daily_limit_raises_quota_error(self):
        def handler(request):
            return httpx.Response(429, text="Rate limit reached for tokens per day (TPD)", headers={"retry-after": "3600"})

        c = OpenAICompatClient("https://x/v1", ["gk"], "m", transport=httpx.MockTransport(handler), sleep=lambda s: None)
        with self.assertRaises(QuotaExhaustedError):
            c.generate_text("hi")

    def test_fallback_moves_from_exhausted_gemini_to_groq(self):
        gem, _ = make_client(lambda r: quota(daily=True))
        groq = OpenAICompatClient(
            "https://x/v1", ["gk"], "m",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "from groq"}}]})),
        )
        chain = FallbackLLM([gem, groq])
        self.assertEqual(chain.generate_text(user_text="hi"), "from groq")

    def test_fallback_pdf_call_skips_text_only_clients(self):
        groq = OpenAICompatClient("https://x/v1", ["gk"], "m", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        with self.assertRaises(LLMError):
            FallbackLLM([groq]).generate_from_pdf(pdf_path="x.pdf", user_text="t")

    def test_fallback_reraises_quota_when_everything_is_out_of_quota(self):
        gem, _ = make_client(lambda r: quota(daily=True))
        with self.assertRaises(QuotaExhaustedError):
            FallbackLLM([gem]).generate_text(user_text="hi")


if __name__ == "__main__":
    unittest.main()
