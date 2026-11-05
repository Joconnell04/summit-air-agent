"""The local names list (voice/names.py, tenants/summit-air/names.yaml) and how it feeds the voice
and the ears."""

import json

import httpx
import pytest
import yaml
from livekit.plugins import cartesia, deepgram

from tests.tool_server.toolkit import TENANT_DIR
from voice import names as local_names
from voice.livekit.settings import load_voice
from voice.livekit.worker import (
    build_stt,
    check_names,
    stt_options,
    tts_options,
    turn_handling,
)

NAMES = local_names.load(TENANT_DIR)
RAW = yaml.safe_load((TENANT_DIR / "names.yaml").read_text())


def name(written: str) -> local_names.Name:
    return next(n for n in NAMES.names if n.written == written)


def test_the_summit_air_list_is_the_fifteen_sourced_names_and_valid():
    assert local_names.problems(NAMES) == []
    assert len(NAMES.names) == 15
    assert all(entry["sources"] for entry in RAW["names"])


def test_kosciuszko_is_said_one_way_and_heard_three_ways():
    kosciuszko = name("Kosciuszko Street")
    assert kosciuszko.says == "koss-ee-OSS-koh"
    assert set(kosciuszko.heard_as) == {"kuh-SHOOS-koh", "kah-skee-OOS-koh"}
    assert "Kosciuszko Street" in NAMES.keyterms()


def test_houston_is_replaced_only_as_a_street():
    houston = name("Houston Street")
    assert houston.says == "HOW-stun Street"
    keys = [item["text"] for item in NAMES.cartesia_items()]
    assert not any(local_names.matches(k, "I just moved here from Houston, Texas") for k in keys)
    assert any(local_names.matches(k, "412 Houston St, New York") for k in keys)


def test_every_dictionary_entry_is_case_sensitive_and_catches_the_mocks_spelling():
    items = NAMES.cartesia_items()
    assert all(item["case_sensitive"] for item in items)
    # The mock's seed writes "Dekalb Ave"; Deepgram writes "DeKalb".
    for spelled in ("412 Dekalb Ave", "412 DeKalb Avenue"):
        assert any(local_names.matches(item["text"], spelled) for item in items), spelled


def test_case_matching_follows_cartesias_rule():
    assert local_names.matches("dekalb", "Dekalb Ave")
    assert not local_names.matches("dekalb", "DEKALB AVE")
    assert not local_names.matches("DeKalb", "Dekalb Ave")
    assert not local_names.matches("todt", "Todthill")


@pytest.mark.parametrize(
    "entry, problem",
    [
        ({"ipa": "<<ˈ|k|ʌ|p>>"}, "not in Sonic 3 English"),
        ({"ipa": "<<k|æ|p>>"}, "stress mark"),
        ({"ipa": "kap"}, "<<...>> groups"),
        ({"match": ["houston"]}, "catches the written form"),
    ],
)
def test_a_bad_entry_is_reported(entry, problem):
    good = {"written": "Cap Street", "match": ["cap"], "ipa": "<<ˈ|k|æ|p>>", "says": "CAP"}
    names = local_names.Names([local_names.Name(**{**good, **entry})])
    assert any(problem in p for p in local_names.problems(names))


def test_too_many_keyterms_for_deepgram_is_reported():
    many = [local_names.Name(f"Street{i}", [f"street{i}"], "<<ˈ|s|t>>", "ST") for i in range(101)]
    assert any("over Deepgram's 100" in p for p in local_names.problems(local_names.Names(many)))


def test_an_over_limit_list_stops_the_worker_at_start(tmp_path):
    folder = tmp_path / "tenants" / "big-city-heat"
    folder.mkdir(parents=True)
    (folder / "agent.yaml").write_text("{}\n")
    many = [
        {"written": f"Street{i}", "match": [f"street{i}"], "ipa": "<<ˈ|s|t>>", "says": "ST"}
        for i in range(101)
    ]
    (folder / "names.yaml").write_text(yaml.safe_dump({"names": many}, allow_unicode=True))
    with pytest.raises(ValueError, match="over Deepgram's 100"):
        local_names.load(folder)
    with pytest.raises(ValueError, match="over Deepgram's 100"):
        check_names(tmp_path / "tenants")


def test_every_tenant_list_in_the_repo_loads():
    check_names(TENANT_DIR.parent)


def test_a_tenant_without_a_list_gets_none(tmp_path):
    empty = local_names.load(tmp_path)
    assert empty.names == [] and empty.cartesia_dictionary_id is None
    voice = load_voice()
    assert "keyterm" not in stt_options(voice, empty)
    assert "pronunciation_dict_id" not in tts_options(voice, empty)


def test_the_worker_gives_deepgram_the_keyterms_and_cartesia_the_dictionary():
    voice = load_voice()
    stt, tts = stt_options(voice, NAMES), tts_options(voice, NAMES)
    assert stt["keyterm"] == NAMES.keyterms()
    assert tts["pronunciation_dict_id"] == NAMES.cartesia_dictionary_id
    # The plugins accept them as built (no network until speech starts).
    deepgram.STT(**stt, api_key="test")
    cartesia.TTS(**tts, api_key="test")


def test_ink2_gets_the_same_keyterms_and_ends_the_turn_itself():
    voice = load_voice()
    trial = {**voice, "stt": voice["stt_candidates"]["ink-2"]}
    options = stt_options(trial, NAMES)
    assert options == {"model": "ink-2", "keyterm": NAMES.keyterms()}
    assert isinstance(build_stt(trial, NAMES, api_key="test"), cartesia.STT)
    assert turn_handling(trial)["turn_detection"] == "stt"
    # Nova-3 keeps LiveKit's turn detector.
    assert turn_handling(voice)["turn_detection"] != "stt"
    assert isinstance(build_stt(voice, NAMES, api_key="test"), deepgram.STT)


def test_the_voice_settings_reach_the_session():
    voice = load_voice()
    tokenizer = tts_options(voice, NAMES)["tokenizer"]
    assert tokenizer._config.min_sentence_len == voice["tts"]["min_sentence_chars"]
    trial = {**voice, "preemptive_generation": True, "preemptive_tts": True}
    assert turn_handling(trial)["preemptive_generation"] == {"enabled": True, "preemptive_tts": True}


def test_push_creates_the_dictionary_then_updates_it_in_place(tmp_path):
    folder = tmp_path / "summit-air"
    folder.mkdir()
    text = (TENANT_DIR / "names.yaml").read_text()
    lines = [ln for ln in text.splitlines() if not ln.startswith("cartesia_dictionary_id:")]
    (folder / "names.yaml").write_text("\n".join(lines + ["cartesia_dictionary_id: null", ""]))
    sent = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path, json.loads(request.content)))
        assert request.headers["Cartesia-Version"] == local_names.CARTESIA_DICT_VERSION
        return httpx.Response(200, json={"id": "pdict_new"})

    with httpx.Client(transport=httpx.MockTransport(answer)) as http:
        assert local_names.push(folder, "key", http) == "pdict_new"
        assert local_names.load(folder).cartesia_dictionary_id == "pdict_new"
        local_names.push(folder, "key", http)
    (method, path, body), (method2, path2, body2) = sent
    assert (method, path) == ("POST", "/pronunciation-dicts/")
    assert body["items"] == NAMES.cartesia_items() and body["name"]
    assert (method2, path2) == ("PATCH", "/pronunciation-dicts/pdict_new")
    assert "name" not in body2


def test_preemptive_generation_is_off_because_the_server_notes_always_change_the_line():
    """LiveKit keeps an early draft only when the caller's line is unchanged, and the worker adds
    the server's notes to it, so a draft was never used (issue #150)."""
    assert turn_handling(load_voice())["preemptive_generation"]["enabled"] is False


def test_deepgram_is_asked_for_its_final_transcript_at_the_end_of_speech_when_set():
    voice = load_voice()
    off = {**voice, "stt": {**voice["stt"], "finalize_at_end_of_speech": False}}
    on = {**voice, "stt": {**voice["stt"], "finalize_at_end_of_speech": True}}
    assert build_stt(off, NAMES, api_key="test").capabilities.manual_flush is False
    assert build_stt(on, NAMES, api_key="test").capabilities.manual_flush is True


def test_flux_decides_the_turn_and_keeps_the_local_names_as_keyterms():
    from livekit.plugins import deepgram

    voice = load_voice()
    flux = {**voice, "stt": voice["stt_candidates"]["flux"]}
    assert stt_options(flux, NAMES) == {"model": "flux-general-en", "keyterm": NAMES.keyterms()}
    assert isinstance(build_stt(flux, NAMES, api_key="test"), deepgram.STTv2)
    assert turn_handling(flux)["turn_detection"] == "stt"
    eager = {**voice, "stt": {**flux["stt"], "eager_eot_threshold": 0.5}}
    assert stt_options(eager, NAMES)["eager_eot_threshold"] == 0.5
