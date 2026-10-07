"""Compose six actual Grainworks screenshots into a disclosed 60-second edit.

The source pixels are fitted proportionally, never cropped, stretched, or retouched.
No screenshot is synthesized. Temporary frames are removed after encoding.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE = Path(__file__).resolve().parents[2]
OUT = BASE / "docs/factory"
ASSETS = OUT
VIDEO = OUT / "factory-demo-60s.mp4"
FFMPEG = Path("C:/ffmpeg/ffmpeg-2025-07-12-git-35a6de137a-full_build/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
REGULAR = Path("C:/Windows/Fonts/malgun.ttf")
BOLD = Path("C:/Windows/Fonts/malgunbd.ttf")
WIDTH, HEIGHT, SECONDS, FPS = 1920, 1080, 10, 30
PAPER, INK, MUTED = "#f4f6ef", "#253c32", "#65756b"
SAGE, LINE, SCREEN = "#436551", "#cbd6c9", "#e6ece2"
DISCLOSURE = "실제 화면 편집 · 추론 대기 구간 생략 · 가상 데이터"
SCENES = [
 ("factory", "factory-hero.png", "공장 공정부터 확인합니다", "입고·배합·검사·포장 상태를 연결합니다. 실제 설비가 아닌 가상 공정입니다.", "공정"),
 ("desk", "factory-desk.png", "설비에서 Lot 기록으로", "처리 능력·대기·보류 물량을 확인하고 연결된 원본 Lot을 선택합니다.", "설비"),
 ("agent", "quality-agent.png", "실제 Qwen의 조회와 근거", "수분 이상 Lot과 기출하 영향을 읽습니다. AI 서술 전체의 정확도를 보증하지 않습니다.", "근거"),
 ("action", "quality-action.png", "실제 조치는 사람이 결정합니다", "AI가 만든 재검사 검토안은 수동 조치 창에서 확인합니다. 아직 적용하지 않았습니다.", "검토"),
 ("comparison", "factory-comparison.png", "정지 조건은 복사본에서 비교합니다", "MIX-01 10분 정지, 20분 비교의 포장 완료 물량입니다. 실제 출하량이 아닙니다.", "비교"),
 ("site", "whole-site.png", "공정과 출하 영향을 함께 봅니다", "창고·트럭은 같은 Lot의 다른 보기입니다. 물량을 중복 합산하지 않습니다.", "영향"),
]


def font(size, bold=False):
    return ImageFont.truetype(str(BOLD if bold else REGULAR), size)


def sources():
    evidence_file = OUT / "evidence.json"
    evidence = json.loads(evidence_file.read_text(encoding="utf-8")) if evidence_file.is_file() else {}
    selected = []
    for key, default, title, caption, label in SCENES:
        entry = evidence.get("screens", {}).get(key, {})
        relative = entry.get("file")
        path = (OUT / relative).resolve() if relative else (ASSETS / default).resolve()
        if key == "reconcile" and (ASSETS / "04-reconcile-full.png").is_file():
            path = (ASSETS / "04-reconcile-full.png").resolve()
        if OUT.resolve() not in path.parents:
            raise ValueError(f"Source must stay inside the portfolio: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"Actual screenshot is not ready: {path.name}")
        with Image.open(path) as im:
            if im.width < 300 or im.height < 150:
                raise ValueError(f"Screenshot is too small to review: {path.name}, {im.size}")
            size = im.size
        selected.append((path, title, caption, label, size))
    return selected


def wrapped(draw, text, face, max_width):
    lines, current = [], ""
    for word in text.split(" "):
        candidate = (current + " " + word).strip()
        if current and draw.textlength(candidate, font=face) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def frame(scene, index):
    path, title, caption, _, _ = scene
    canvas = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.text((65, 30), "grainworks.", font=font(30, True), fill=SAGE)
    disclosure_face = font(23)
    disclosure_width = draw.textlength(DISCLOSURE, font=disclosure_face)
    draw.text((WIDTH - 65 - disclosure_width, 38), DISCLOSURE, font=disclosure_face, fill=MUTED)
    draw.line((65, 96, WIDTH - 65, 96), fill=LINE, width=2)
    draw.text((65, 121), title, font=font(39, True), fill=INK)
    sequence = f"{index + 1:02d} / {len(SCENES):02d}"
    draw.text((WIDTH - 220, 133), sequence, font=font(24), fill=MUTED)
    panel = (64, 202, WIDTH - 64, 920)
    draw.rounded_rectangle(panel, radius=8, fill=SCREEN, outline=LINE, width=2)
    with Image.open(path) as original:
        image = ImageOps.contain(original.convert("RGB"), (1758, 692), Image.Resampling.LANCZOS)
    x = (WIDTH - image.width) // 2
    y = panel[1] + (panel[3] - panel[1] - image.height) // 2
    draw.rectangle((x-3,y-3,x+image.width+3,y+image.height+3),outline="#52728a",width=3)
    canvas.paste(image, (x, y))
    caption_face = font(26)
    caption_lines = wrapped(draw, caption, caption_face, WIDTH - 130)
    if len(caption_lines) > 2:
        raise ValueError("Caption is too long for the disclosed frame.")
    for line_index, line in enumerate(caption_lines):
        draw.text((65, 944 + line_index * 38), line, font=caption_face, fill=INK)
    progress_y = 1036
    segment_width, gap = 284, 12
    for step, scene_meta in enumerate(SCENES):
        left = 65 + step * (segment_width + gap)
        color = SAGE if step == index else LINE
        draw.rectangle((left, progress_y, left + segment_width, progress_y + 4), fill=color)
        draw.text((left, progress_y - 31), scene_meta[4], font=font(18, step == index), fill=SAGE if step == index else MUTED)
    return canvas


def probe(path):
    result = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames", "-of", "json", str(path)], check=True, capture_output=True, text=True, encoding="utf-8")
    metadata = json.loads(result.stdout)
    video = next(item for item in metadata["streams"] if item["codec_type"] == "video")
    duration = float(metadata["format"]["duration"])
    if abs(duration - 60) > .001 or video["width"] != WIDTH or video["height"] != HEIGHT or video["codec_name"] != "h264":
        raise ValueError(f"Video verification failed: {metadata}")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check actual input files without producing a video")
    args = parser.parse_args()
    selected = sources()
    for path, _, _, _, size in selected:
        print(json.dumps({"source": path.name, "dimensions": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, ensure_ascii=False))
    if args.check:
        print("Ready: 6 actual screenshots, target 60 seconds / 1920x1080 / H.264.")
        return
    if not FFMPEG.is_file() or not FFPROBE.is_file():
        raise FileNotFoundError("The configured FFmpeg/ffprobe runtime is unavailable.")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grainworks-demo-") as temp:
        temp_path = Path(temp)
        paths = []
        for index, scene in enumerate(selected):
            destination = temp_path / f"slide-{index + 1:02d}.png"
            rendered=frame(scene,index)
            rendered.save(destination)
            rendered.resize((640,360)).save(OUT / f"video-frame-{index+1}.png")
            paths.append(destination)
        concat = temp_path / "slides.txt"
        entries = []
        for path in paths:
            entries.extend([f"file '{path.as_posix()}'", f"duration {SECONDS}"])
        entries.append(f"file '{paths[-1].as_posix()}'")
        concat.write_text("\n".join(entries) + "\n", encoding="utf-8")
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-an", "-vf", f"fps={FPS},tpad=stop_mode=clone:stop_duration=1", "-frames:v", str(len(selected) * SECONDS * FPS), "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(VIDEO)], check=True)
    metadata = probe(VIDEO)
    print(json.dumps({"output": str(VIDEO), "verified": metadata}, ensure_ascii=False))


if __name__ == "__main__":
    main()
