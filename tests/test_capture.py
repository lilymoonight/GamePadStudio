from pathlib import Path
from gamepadstudio import screenshot_service as service


class FakeMSS:
    monitors=[{'left':-1920,'top':0,'width':3840,'height':1080},{'left':0,'top':0,'width':1920,'height':1080}]
    captures=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def grab(self,box):
        self.captures.append(box)
        class Shot:
            rgb=b'\x00'*12;size=(2,2);width=2;height=2
        return Shot()


def test_capture_unique_names_modes_and_metadata(tmp_path,monkeypatch):
    FakeMSS.captures=[]
    monkeypatch.setattr(service.mss,'mss',FakeMSS)
    monkeypatch.setattr(service,'foreground_info',lambda:(None,'测试窗口'))
    monkeypatch.setattr(service,'_get_active_monitor_bbox',lambda:{'left':-1920,'top':0,'width':1920,'height':1080})
    first=service.take_screenshot(tmp_path)
    second=service.take_screenshot(tmp_path,mode='all')
    assert first!=second and Path(first).is_file() and Path(second).is_file()
    assert FakeMSS.captures[0]['left']==-1920
    assert FakeMSS.captures[1]['width']==3840
    # Option B: Verify no .json sidecar files are generated
    assert not Path(first).with_suffix('.json').exists()
    assert not Path(second).with_suffix('.json').exists()
    # Verify embedded PNG metadata is correctly extracted
    rows=service.list_captures(tmp_path)
    assert len(rows)==2 and all(r['title']=='测试窗口' and r['width']==2 for r in rows)
    # Test setting favorite on native PNG
    service.set_favorite(first, True)
    updated_rows = service.list_captures(tmp_path)
    first_row = next(r for r in updated_rows if r['path'] == first)
    assert first_row['favorite'] is True
    assert not Path(first).with_suffix('.json').exists()


def test_delete_capture(tmp_path, monkeypatch):
    FakeMSS.captures = []
    monkeypatch.setattr(service.mss, 'mss', FakeMSS)
    monkeypatch.setattr(service, 'foreground_info', lambda: (None, '测试窗口'))
    monkeypatch.setattr(service, '_get_active_monitor_bbox', lambda: {'left': 0, 'top': 0, 'width': 1920, 'height': 1080})
    path = service.take_screenshot(tmp_path)
    img = Path(path)
    sidecar = img.with_suffix('.json')
    assert img.is_file()
    assert not sidecar.exists()  # Option B: zero sidecar clutter
    assert len(service.list_captures(tmp_path)) == 1

    # Also test legacy sidecar cleanup if present
    sidecar.write_text('{"title": "legacy"}', encoding='utf-8')
    assert sidecar.exists()

    # Delete
    assert service.delete_capture(path) is True
    assert not img.exists()
    assert not sidecar.exists()
    assert len(service.list_captures(tmp_path)) == 0

    # Deleting already deleted file returns False
    assert service.delete_capture(path) is False


def test_list_and_delete_mp4_captures(tmp_path):
    vid = tmp_path / 'DS_测试游戏_20260929_120000_replay.mp4'
    vid.write_bytes(b'\x00' * 2048)
    thumb = tmp_path / 'DS_测试游戏_20260929_120000_replay.jpg'
    thumb.write_bytes(b'\x01' * 512)

    rows = service.list_captures(tmp_path)
    assert len(rows) == 1
    assert rows[0]['is_video'] is True
    assert '测试游戏' in rows[0]['title']
    assert rows[0]['thumb_path'] == str(thumb)

    # Favorite MP4
    service.set_favorite(str(vid), True)
    updated = service.list_captures(tmp_path)
    assert updated[0]['favorite'] is True

    # Delete MP4 and thumbnail
    assert service.delete_capture(str(vid)) is True
    assert not vid.exists()
    assert not thumb.exists()
    assert len(service.list_captures(tmp_path)) == 0

