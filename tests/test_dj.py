from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from core import analysis
from core.camelot import camelot_label, harmonic_order, key_relation, match, to_camelot
from core.crates import LibraryStore
from core.soulseek import Candidate, DownloadHistory
from ui import aggregator_view as module


# ============================== Camelot ==============================

@pytest.mark.parametrize("text, code", [
    ("Am", "8A"), ("A minor", "8A"), ("C", "8B"), ("C major", "8B"), ("F#m", "11A"),
    ("Gbm", "11A"), ("Dbm", "12A"), ("Eb", "5B"), ("B", "1B"), ("Fm", "4A"),
    ("8A", "8A"), ("08b", "8B"), ("1m", "8A"), ("1d", "8B"), ("12d", "7B"), ("A♭", "4B"),
    ("", None), ("lixo", None), (None, None),
])
def test_to_camelot(text, code):
    assert to_camelot(text) == code


def test_camelot_label_roundtrip():
    for name in ("C", "G", "D", "A", "E", "B", "F#", "Ab", "Eb", "Bb", "F",
                 "Am", "Em", "Bm", "Dm", "Gm", "Cm", "Fm"):
        assert to_camelot(camelot_label(to_camelot(name))) == to_camelot(name)


def test_key_relations():
    assert key_relation("8A", "8A")[1] == "mesmo tom"
    assert key_relation("8A", "9A")[1].startswith("+1")
    assert key_relation("12A", "1A")[1].startswith("+1")  # a roda dá a volta
    assert key_relation("8A", "8B")[1] == "relativo maior/menor"
    assert key_relation("8A", "3B")[1] == "choque de tom"


def test_match_handles_double_time():
    m = match("8A", 87, "8A", 174)
    assert m.bpm_reason == "dobro" and m.harmonic


def test_harmonic_order_chains_neighbours():
    tracks = [{"id": i, "camelot": c, "bpm": 124} for i, c in
              enumerate(["8A", "3B", "9A", "2B", "10A"])]
    ordered = [t["camelot"] for t in harmonic_order(tracks)]
    assert ordered[:3] == ["8A", "9A", "10A"]


# ============================== análise ==============================

def _synth(path, bpm=126, root_pc=9, minor=True, seconds=40, sr=22050):
    n = int(seconds * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    kick_len = int(0.2 * sr)
    kt = np.arange(kick_len) / sr
    kick = np.sin(2 * np.pi * (50 + 80 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 12)
    for i in range(int(seconds * bpm / 60)):
        s = int(i * 60 / bpm * sr)
        y[s:s + kick_len] += kick[: n - s]
    third = 3 if minor else 4
    for off in (0, third, 7):
        f = 220 * 2 ** ((root_pc - 9 + off) / 12)
        y += 0.1 * np.sin(2 * np.pi * f * t)
    sf.write(path, (y / np.abs(y).max() * 0.8).astype(np.float32), sr)


def test_analyze_audio(tmp_path):
    path = str(tmp_path / "a.wav")
    _synth(path)
    result = analysis.analyze(path)
    assert result.bpm == 126
    assert result.camelot == "8A"
    assert result.source == "áudio"


def test_tags_win_over_audio(tmp_path):
    from mutagen.flac import FLAC
    path = str(tmp_path / "tagged.flac")
    _synth(path)
    audio = FLAC(path)
    audio["bpm"] = "122"
    audio["initialkey"] = "5A"
    audio.save()
    result = analysis.analyze(path)
    assert (result.bpm, result.camelot, result.source) == (122.0, "5A", "tags")


# ============================== crates ==============================

def test_crate_store(tmp_path):
    store = LibraryStore(tmp_path / "lib.db")
    cid = store.create_crate("Warm-up")
    assert store.add_to_crate(cid, "a.flac", "A")
    assert store.add_to_crate(cid, "b.flac", "B")
    assert not store.add_to_crate(cid, "a.flac", "A")  # sem repetir
    b = store.crate_tracks(cid)[1]["id"]
    store.move(cid, b, -1)
    assert [t["label"] for t in store.crate_tracks(cid)] == ["B", "A"]
    assert store.crates()[0]["count"] == 2
    store.delete_crate(cid)
    assert store.crates() == []


def test_manual_correction_is_never_overwritten(tmp_path):
    store = LibraryStore(tmp_path / "lib.db")
    song = tmp_path / "s.wav"
    song.write_bytes(b"x")
    assert store.needs_analysis(str(song))
    store.save_analysis(str(song), 128, "8A", "manual")
    song.write_bytes(b"changed")
    assert not store.needs_analysis(str(song))


# ============================== tela ==============================

@pytest.fixture
def view(monkeypatch, tmp_path):
    page = SimpleNamespace(overlay=[], update=lambda: None)
    monkeypatch.setattr(module.AggregatorView, "_queue_analysis",
                        lambda self, paths, delay=0: None)
    v = module.AggregatorView(page)
    v.engine.history = DownloadHistory(tmp_path / "history.db")
    tracks = {"Um - Faixa": ("8A", 124), "Dois - Faixa": ("9A", 125),
              "Tres - Faixa": ("3B", 140), "Quatro - Faixa": ("8B", 123)}
    for i, (name, (code, bpm)) in enumerate(tracks.items()):
        f = tmp_path / f"{i}.wav"
        f.write_bytes(b"x")
        v.engine.history.add(f"{name} · WAV", Candidate(username="u", remote_path=str(i)), str(f))
        v.store.save_analysis(str(f), bpm, code, "áudio")
    v._analysis = v.store.all_analysis()
    v.control.content = v._main_layout
    v._go_to(module.NAV_LIBRARY)
    yield v
    v.shutdown()


def _path_of(view, title):
    return next(e["local_path"] for e in view._library if e["label"].startswith(title))


def test_suggestions_rank_harmonic_tracks_first(view):
    picks = [e["label"] for e, m in view._suggestions(_path_of(view, "Um"))]
    assert picks[0].startswith("Dois") or picks[0].startswith("Quatro")
    assert not any(p.startswith("Tres") for p in picks)  # 3B a 140 BPM não mixa


def test_library_sort_and_filter_by_key(view):
    view.library_sort.value = "key"
    view.library_filter.value = ""
    sorted_codes = [view._track_meta(e["local_path"])[1] for e in view._sort_library(view._library)]
    assert sorted_codes == ["3B", "8A", "8B", "9A"]
    assert [e["label"] for e in view._library if view._library_matches(e, "8a")] == ["Um - Faixa · WAV"]


def test_crate_flow(view):
    cid = view.store.create_crate("Pico")
    view._go_to(module.NAV_CRATES)
    view._reload_crates()
    for title in ("Tres", "Um", "Quatro", "Dois"):
        view._add_to_crate(cid, _path_of(view, title), title)
    summary = view.crate_detail.controls[0].controls[0].controls[1].value
    # 3B -> 8A choca; 8A -> 8B é relativo (ok); 8B -> 9A choca.
    assert "2 transições pra revisar" in summary
    view._harmonic_sort(cid)
    order = [t["label"] for t in view.store.crate_tracks(cid)]
    # A 1ª fica no lugar; de 8A o melhor vizinho é 9A (+1, BPM mais perto).
    assert order == ["Tres", "Um", "Dois", "Quatro"]


def test_edit_analysis_dialog_saves_manual(view):
    path = _path_of(view, "Tres")
    view._edit_analysis(path, "Tres")
    dialog = view._dialog
    bpm_field, key_field = dialog.content.controls[:2]
    bpm_field.value, key_field.value = "141,5", "Dbm"
    dialog.actions[1].on_click(None)
    assert view._track_meta(path) == (141.5, "12A")
    assert view.store.analysis_for(path)["source"] == "manual"
    assert view._dialog is None


def test_edit_analysis_rejects_garbage(view):
    path = _path_of(view, "Tres")
    view._edit_analysis(path, "Tres")
    dialog = view._dialog
    dialog.content.controls[1].value = "xyz"
    dialog.actions[1].on_click(None)
    assert view._dialog is dialog  # continua aberto, com erro
    assert dialog.content.controls[-1].visible


# ============================== desempenho ==============================

def test_play_pause_does_not_rebuild_the_library(view):
    rows_before = list(view.library_list.controls)
    path = _path_of(view, "Um")
    btn = next(iter(view._play_buttons[f"file:{path}"]))
    view._playing_key = f"file:{path}"
    view._refresh_play_icons()
    assert view.library_list.controls == rows_before  # mesmas linhas, nada refeito
    assert btn.icon == module.ft.icons.PAUSE_CIRCLE_ROUNDED


def test_analysis_updates_only_that_tracks_pills(view):
    path = _path_of(view, "Tres")
    rows_before = list(view.library_list.controls)
    view.store.save_analysis(path, 99, "1A", "manual")
    view._analysis[path] = view.store.analysis_for(path)
    view._refresh_pills(path)
    assert view.library_list.controls == rows_before
    pills = next(iter(view._pill_rows[path]))
    assert pills.controls[1].tooltip.startswith("Camelot 1A")


def test_color_filter_with_and_without_neighbours(view):
    view._set_color_filter(8)
    assert view.library_count.value == "2 de 4"          # 8A e 8B
    view.color_neighbors.value = True
    view._reset_library_paging()
    assert view.library_count.value == "3 de 4"          # + 9A (3B fica de fora)
    view._set_color_filter(8)                            # clicar de novo limpa
    assert view.library_count.value == "4 músicas"


def test_library_renders_in_pages(view, tmp_path):
    for i in range(module.LIBRARY_PAGE + 5):
        f = tmp_path / f"extra{i}.wav"
        f.write_bytes(b"x")
        view.engine.history.add(f"Extra {i} · WAV", Candidate(username="x", remote_path=f"e{i}"), str(f))
    view._reload_library()
    rows = view.library_list.controls
    assert len(rows) == module.LIBRARY_PAGE + 1          # + botão "Mostrar mais"
    view._show_more_library()
    assert len(view.library_list.controls) == len(view._library)


def test_library_is_not_drawn_until_its_tab_opens(monkeypatch, tmp_path):
    monkeypatch.setattr(module.AggregatorView, "_queue_analysis",
                        lambda self, paths, delay=0: None)
    v = module.AggregatorView(SimpleNamespace(overlay=[], update=lambda: None))
    assert v.library_list.controls == []
    v.shutdown()
