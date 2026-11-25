"""Face-following crop for layout panels with "fit": "track".

Idea from AI-Youtube-Shorts-Generator (MIT): detect the largest face with OpenCV and slide the
crop window to keep it centred, with smoothing. Here the face path is sampled at a few fps,
smoothed (dead-zone + easing), interpolated to every output frame and fed to ffmpeg's crop filter
through `sendcmd`, so tracking works together with layouts, silence cuts and burned-in text.

Panel options (in a layout):
  {"src": [x, y, w, h], "dst": [...], "fit": "track",
   "detector": "face" | "anime",        # anime = lbpcascade_animeface (VTuber avatars)
   "smooth": 0.35, "deadzone": 0.06}     # easing per sample, dead-zone as a share of the region

Without OpenCV (or when no face is found) the window simply stays centred.
"""
from __future__ import annotations

from pathlib import Path

from common import CONFIG_DIR

ANIME_CASCADE = CONFIG_DIR / "models" / "lbpcascade_animeface.xml"
YUNET = CONFIG_DIR / "models" / "face_detection_yunet_2023mar.onnx"
ANIME_CASCADE_URL = "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml"


def window_size(region_w: int, region_h: int, dst_w: int, dst_h: int) -> tuple[int, int]:
    aspect = dst_w / dst_h
    if region_w / region_h > aspect:
        h = region_h
        w = int(h * aspect)
    else:
        w = region_w
        h = int(w / aspect)
    return max(2, w - w % 2), max(2, h - h % 2)


def _make_detector(kind: str):
    """Return (name, fn(gray, bgr) -> [(x, y, w, h)]).

    - anime: lbpcascade_animeface (needs cv2.CascadeClassifier — OpenCV 4.x)
    - face : YuNet DNN detector (cv2.FaceDetectorYN, OpenCV >= 4.5.4 incl. 5.x) if its model is
             installed, else the Haar frontal/profile cascades shipped with OpenCV 4.x
    """
    import cv2
    has_cascade = hasattr(cv2, "CascadeClassifier")
    if kind == "anime":
        if ANIME_CASCADE.exists() and has_cascade:
            c = cv2.CascadeClassifier(str(ANIME_CASCADE))
            return "anime-cascade", lambda g, bgr, ms: [tuple(map(int, f)) for f in
                                                        c.detectMultiScale(g, 1.1, 5, minSize=(ms, ms))]
        print("  [track] アニメ顔検出が使えないため通常の顔検出で代用します"
              "（check_env.py --install-models / pip install \"opencv-python<5\"）")
    if YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
        state = {}

        def yunet(g, bgr, ms):
            h, w = bgr.shape[:2]
            if "d" not in state:
                state["d"] = cv2.FaceDetectorYN.create(str(YUNET), "", (w, h), 0.6, 0.3, 50)
            state["d"].setInputSize((w, h))
            _, faces = state["d"].detect(bgr)
            return [] if faces is None else [tuple(int(v) for v in f[:4]) for f in faces if f[2] >= ms]
        return "yunet", yunet
    data = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if has_cascade and data:
        cs = [cv2.CascadeClassifier(data + n) for n in ("haarcascade_frontalface_default.xml",
                                                         "haarcascade_profileface.xml")]

        def haar(g, bgr, ms):
            for c in cs:
                found = c.detectMultiScale(g, 1.1, 5, minSize=(ms, ms))
                if len(found):
                    return [tuple(map(int, f)) for f in found]
            return []
        return "haar", haar
    raise ImportError("no face detector (run check_env.py --install-models)")


def sample_faces(src: Path, start: float, dur: float, region, kind: str = "face", rate: float = 5.0):
    """Return [(t_rel, cx, cy) or (t_rel, None, None)] with face centres in source pixels."""
    import cv2
    rx, ry, rw, rh = region
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    step = max(1, round(fps / rate))
    det_name, detect = _make_detector(kind)
    scale = min(1.0, 640 / rw)
    out, n = [], 0
    prev = None
    while n / fps <= dur:
        ok = cap.grab()
        if not ok:
            break
        if n % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            roi = frame[ry:ry + rh, rx:rx + rw]
            small = cv2.resize(roi, None, fx=scale, fy=scale) if scale < 1 else roi
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = detect(gray, small, int(24 * max(scale, 0.5)))
            if faces:
                # largest face, but prefer staying on the previous subject when sizes are close
                def key(f):
                    x, y, w, h = f
                    c = (x + w / 2, y + h / 2)
                    dist = 0 if prev is None else ((c[0] - prev[0]) ** 2 + (c[1] - prev[1]) ** 2) ** 0.5
                    return w * h - 0.5 * dist * (w + h) / 2
                x, y, w, h = max(faces, key=key)
                cx, cy = x + w / 2, y + h / 2
                prev = (cx, cy)
                out.append((n / fps, rx + cx / scale, ry + cy / scale))
            else:
                out.append((n / fps, None, None))
        n += 1
    cap.release()
    sample_faces.last_detector = det_name
    return out


def smooth_path(samples, region, win, smooth=0.35, deadzone=0.06):
    """Dead-zone + easing; returns [(t_rel, x, y)] window top-left in source pixels."""
    rx, ry, rw, rh = region
    ww, wh = win
    cx, cy = rx + rw / 2, ry + rh / 2
    tx, ty = cx, cy
    path = []
    for t, fx, fy in samples:
        if fx is not None:
            if abs(fx - tx) > deadzone * rw:
                tx = fx
            if abs(fy - ty) > deadzone * rh:
                ty = fy
        cx += (tx - cx) * smooth
        cy += (ty - cy) * smooth
        x = min(max(cx - ww / 2, rx), rx + rw - ww)
        y = min(max(cy - wh / 2, ry), ry + rh - wh)
        path.append((t, int(x) // 2 * 2, int(y) // 2 * 2))
    return path


def write_sendcmd(path_pts, keep, fps: int, target: str, out_file: Path) -> None:
    """Interpolate the path to every output frame, mapping source time through `keep`."""
    if not path_pts:
        out_file.write_text("", encoding="utf-8")
        return
    ts = [p[0] for p in path_pts]

    def at(t):
        if t <= ts[0]:
            return path_pts[0][1:]
        for i in range(1, len(ts)):
            if t <= ts[i]:
                a, b = path_pts[i - 1], path_pts[i]
                k = (t - a[0]) / max(b[0] - a[0], 1e-6)
                return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k
        return path_pts[-1][1:]

    lines, out_t = [], 0.0
    last = None
    for ka, kb in keep:
        t = ka
        while t < kb:
            x, y = at(t)
            xy = (int(x) // 2 * 2, int(y) // 2 * 2)
            if xy != last:
                lines.append(f"{out_t:.3f} {target} x {xy[0]}, {target} y {xy[1]};")
                last = xy
            t += 1 / fps
            out_t += 1 / fps
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_tracks(layout: dict, src: Path, info: dict, start: float, dur: float, keep, fps: int,
                   workdir: Path, tag: str) -> dict:
    """Compute sendcmd files for every "track" panel. Returns {panel_index: (file_name, x0, y0)}."""
    res = {}
    W, H = info["width"], info["height"]
    for i, p in enumerate(layout.get("panels", []), start=1):
        if p.get("fit") != "track":
            continue
        rx, ry, rw, rh = p["src"]
        region = (int(W * rx), int(H * ry), int(W * rw) // 2 * 2, int(H * rh) // 2 * 2)
        win = window_size(region[2], region[3], p["dst"][2], p["dst"][3])
        x0 = region[0] + (region[2] - win[0]) // 2 // 2 * 2
        y0 = region[1] + (region[3] - win[1]) // 2 // 2 * 2
        name = f"trk_{tag}_{i}.txt"
        try:
            samples = sample_faces(src, start, dur, region, p.get("detector", "face"))
        except ImportError:
            print("  [track] OpenCV が無いので中央固定にします（pip install opencv-python）")
            samples = []
        found = sum(1 for s in samples if s[1] is not None)
        pts = smooth_path(samples, region, win, p.get("smooth", 0.35), p.get("deadzone", 0.06)) if found else []
        if pts:
            write_sendcmd(pts, keep, fps, f"crop@trk{i}", workdir / name)
            x0, y0 = pts[0][1], pts[0][2]
        else:
            name = None
        print(f"  [track] panel{i}: 顔検出 {found}/{len(samples)} サンプル"
              f"（{getattr(sample_faces, 'last_detector', p.get('detector', 'face'))}）")
        res[i] = (name, x0, y0, win)
    return res
