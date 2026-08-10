from tools.tracker10.format import Frame, Voice, encode_track, inspect_track, pack_playlist


def test_track_and_playlist_headers():
    voices=tuple(Voice(1000+i,10,i%8) for i in range(10))
    track=encode_track([Frame(0,voices,0x3ff)],3200,0)
    image=pack_playlist([track])
    assert track[:4]==b"T10M"
    assert image[:4]==b"T10P"
    assert image[6:8]==b"\x01\x00"
    assert inspect_track(track)["samples"]==3200


def test_compact_pitch_event_is_used():
    a=tuple(Voice(1000,10,0) for _ in range(10))
    b=list(a); b[3]=Voice(1200,10,0)
    track=encode_track([Frame(0,a,0x3ff),Frame(100,tuple(b),0)],200,0)
    assert 0x73 in track
