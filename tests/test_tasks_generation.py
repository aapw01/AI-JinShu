from app.tasks.generation import _volume_chunks


def test_volume_chunks_split():
    chunks = _volume_chunks(start_chapter=1, num_chapters=65, volume_size=30)
    assert chunks == [(1, 1, 30), (2, 31, 30), (3, 61, 5)]


def test_volume_chunks_min_volume_size():
    chunks = _volume_chunks(start_chapter=10, num_chapters=3, volume_size=0)
    assert chunks == [(1, 10, 3)]
