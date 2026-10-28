import pytest

from app.ingestion.chunking import chunk_pages
from tests.helpers import words


def test_chunk_pages_uses_window_and_overlap():
    text = words("w", 700)
    chunks = chunk_pages([text], chunk_words=300, overlap_words=50)

    assert [c.word_count for c in chunks] == [300, 300, 200]
    first, second, third = (c.text.split() for c in chunks)
    assert first[0] == "w000" and second[0] == "w250" and third[0] == "w500"
    assert first[-50:] == second[:50]
    assert second[-50:] == third[:50]
    assert third[-1] == "w699"


def test_chunk_pages_tracks_page_span():
    chunks = chunk_pages([words("a", 200), words("b", 200)], chunk_words=300, overlap_words=50)

    assert [(c.page_start, c.page_end) for c in chunks] == [(1, 2), (2, 2)]
    assert chunks[1].text.split()[0] == "b050"


def test_chunk_pages_edge_cases():
    assert chunk_pages([]) == []
    assert chunk_pages(["", "   \n "]) == []
    single = chunk_pages(["just a few words"])
    assert len(single) == 1 and single[0].word_count == 4
    # Exactly one window: no trailing chunk made only of overlap.
    assert len(chunk_pages([words("x", 300)])) == 1
    with pytest.raises(ValueError):
        chunk_pages(["a b c"], chunk_words=50, overlap_words=50)


def test_upload_pdf_creates_chunks(client, auth, make_pdf):
    pdf = make_pdf([words("a", 200), words("b", 200)])
    resp = client.post(
        "/sources/pdf",
        files={"file": ("intro.pdf", pdf, "application/pdf")},
        data={"title": "Intro to ML"},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    source = resp.json()
    assert source["kind"] == "pdf"
    assert source["status"] == "ready"
    assert source["title"] == "Intro to ML"
    assert source["metadata"]["page_count"] == 2
    assert source["metadata"]["word_count"] == 400
    assert source["metadata"]["chunk_count"] == 2

    resp = client.get(f"/sources/{source['id']}/chunks", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    first, second = body["items"]
    assert (first["chunk_index"], first["page_number"], first["page_end"]) == (0, 1, 2)
    assert (second["chunk_index"], second["page_number"], second["page_end"]) == (1, 2, 2)
    assert first["word_count"] == 300 and second["word_count"] == 150
    assert first["text"].startswith("a000") and second["text"].endswith("b199")
    assert first["start_sec"] is None


def test_title_defaults_to_filename(client, auth, make_pdf):
    resp = client.post(
        "/sources/pdf", files={"file": ("week-1 notes.pdf", make_pdf(["hello world"]))}, headers=auth
    )
    assert resp.json()["title"] == "week-1 notes"


def test_blank_pdf_needs_ocr(client, auth, make_pdf):
    resp = client.post("/sources/pdf", files={"file": ("scan.pdf", make_pdf(["", ""]))}, headers=auth)
    assert resp.status_code == 201
    assert resp.json()["status"] == "needs_ocr"


def test_rejects_invalid_and_non_pdf_files(client, auth, settings):
    resp = client.post("/sources/pdf", files={"file": ("broken.pdf", b"not a pdf")}, headers=auth)
    assert resp.status_code == 400
    resp = client.post("/sources/pdf", files={"file": ("notes.txt", b"hello")}, headers=auth)
    assert resp.status_code == 415

    assert client.get("/sources", headers=auth).json()["total"] == 0
    # Failed uploads leave no files behind.
    assert not any(p.is_file() for p in settings.upload_dir.rglob("*"))


def test_list_and_get_sources(client, auth, make_pdf):
    for name in ("one.pdf", "two.pdf"):
        client.post("/sources/pdf", files={"file": (name, make_pdf(["some text"]))}, headers=auth)

    listing = client.get("/sources", params={"kind": "pdf", "status": "ready"}, headers=auth).json()
    assert listing["total"] == 2
    assert {s["title"] for s in listing["items"]} == {"one", "two"}
    assert client.get("/sources", params={"kind": "video"}, headers=auth).json()["total"] == 0

    source_id = listing["items"][0]["id"]
    assert client.get(f"/sources/{source_id}", headers=auth).json()["id"] == source_id
    assert client.get("/sources/does-not-exist", headers=auth).status_code == 404


def test_sources_are_tenant_isolated(client, auth, other_auth, make_pdf):
    source = client.post("/sources/pdf", files={"file": ("a.pdf", make_pdf(["secret"]))}, headers=auth).json()

    assert client.get(f"/sources/{source['id']}", headers=other_auth).status_code == 404
    assert client.get(f"/sources/{source['id']}/chunks", headers=other_auth).status_code == 404
    assert client.get("/sources", headers=other_auth).json()["total"] == 0


def test_requires_api_key(client, make_pdf):
    assert client.get("/sources").status_code == 401
    assert client.get("/sources", headers={"X-API-Key": "aisu_wrong"}).status_code == 401
