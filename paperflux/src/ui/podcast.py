"""The "Listen" panel: a paper's two-host audio conversation, with its transcript."""
from typing import Any, Callable, Optional

import streamlit as st

READY = "ready"
IN_PROGRESS = {"scripted", "failed"}


def has_podcast(paper: dict[str, Any]) -> bool:
    pod = paper.get("podcast") or {}
    return pod.get("status") == READY and bool(pod.get("audio_id"))


@st.cache_data(show_spinner=False, max_entries=12, ttl=3600)
def _audio(audio_id: str, _load: Callable[[str], Optional[bytes]]) -> Optional[bytes]:
    return _load(audio_id)


def _length(pod: dict[str, Any]) -> str:
    seconds = pod.get("duration_s") or (pod.get("minutes") or 0) * 60
    return f"{max(1, round(seconds / 60))} min" if seconds else ""


def render_podcast(paper: dict[str, Any], load_audio: Optional[Callable[[str], Optional[bytes]]]) -> None:
    pod = paper.get("podcast") or {}
    status = pod.get("status")
    if status in IN_PROGRESS:
        st.caption("🎧 An audio conversation about this paper is being recorded. It will appear here.")
        return
    if not has_podcast(paper) or load_audio is None:
        return
    pid = paper["paper_id"]
    names = [h.get("name", "") for h in pod.get("hosts") or []]
    with st.container(border=True):
        st.markdown(f"**🎧 Listen: {pod.get('title') or 'The conversation'}**")
        details = [_length(pod), " and ".join(n for n in names if n) + " talk through this paper" if names else ""]
        st.caption(" · ".join(d for d in details if d))
        data = _audio(pod["audio_id"], load_audio)
        if not data:
            st.caption("The audio is not available right now. Please try again later.")
            return
        mime = pod.get("mime") or "audio/mpeg"
        st.audio(data, format=mime)
        transcript, download = st.columns([3, 1], vertical_alignment="center")
        with transcript.expander("Read the transcript"):
            if pod.get("summary"):
                st.caption(pod["summary"])
            for turn in pod.get("turns") or []:
                st.markdown(f"**{turn.get('speaker', '')}:** {turn.get('text', '')}")
        download.download_button(
            "Episode",
            data=data,
            file_name=f"paperflux-{pid}.{'mp3' if mime == 'audio/mpeg' else 'wav'}",
            mime=mime,
            icon=":material/download:",
            on_click="ignore",
            key=f"dl-podcast-{pid}",
            width="stretch",
        )
