"""Pure builders for the ffmpeg ``-filter_complex`` graph and command line (no I/O)."""

from __future__ import annotations

from collections.abc import Callable

from .types import Cut, EditPlan, MontageSettings

GRADE = "hqdn3d=1.5:1.5:4:4,eq=contrast=1.06:saturation=1.15:gamma=1.04,unsharp=5:5:0.5:5:5:0.0"
# gamma > 1 lifts shadows slightly so night footage keeps its detail instead of crushing.
BG_DARKEN = "eq=brightness=-0.12:saturation=0.85"  # blurred background sits behind the subject
AUDIO_EDGE_S = 0.015  # micro fade at every cut to avoid clicks
DUCK = "sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400"


def escape_filter_path(path: str) -> str:
    """Quote a file path for a filter option (Windows ``C:\\x`` → ``'C\\:/x'``)."""
    p = path.replace("\\", "/").replace(":", r"\:").replace("'", r"'\\\''")
    return f"'{p}'"


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


# --- reframe strategies: each maps one cut's frames to exactly WxH -------------------


def crop_reframe(label: str, cut: Cut, plan: EditPlan, w: int, h: int) -> str:
    """Stationary 9:16 crop centred on the cut's ``crop_x`` (else the plan's), then scale."""
    cx = next((x for x in (cut.crop_x, plan.crop_x) if x is not None), 0.5)
    return (
        f"crop=w='trunc(min(iw,ih*{w}/{h})/2)*2':h='trunc(min(ih,iw*{h}/{w})/2)*2'"
        f":x='max(0,min(iw-ow,{cx:.3f}*iw-ow/2))':y='(ih-oh)/2',scale={w}:{h}"
    )


def pad_reframe(label: str, cut: Cut, plan: EditPlan, w: int, h: int) -> str:
    """Fit the whole frame and letterbox."""
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def blur_reframe(label: str, cut: Cut, plan: EditPlan, w: int, h: int) -> str:
    """Sharp fitted foreground over a blurred, darkened fill of the same frame."""
    bw, bh = _even(w / 4), _even(h / 4)
    radius = max(1, min(10, bw // 8))
    return (
        f"split=2[{label}bg][{label}fg];"
        f"[{label}bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
        f"boxblur={radius}:2,{BG_DARKEN},scale={w}:{h}[{label}bb];"
        f"[{label}fg]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[{label}ff];"
        f"[{label}bb][{label}ff]overlay=(W-w)/2:(H-h)/2"
    )


def smart_reframe(label: str, cut: Cut, plan: EditPlan, w: int, h: int) -> str:
    """Crop near-vertical sources, blur-fill wide ones (the planner usually resolves this)."""
    wide = plan.source.width / max(plan.source.height, 1) > w / h * 1.2
    return (blur_reframe if wide else crop_reframe)(label, cut, plan, w, h)


REFRAMES: dict[str, Callable[[str, Cut, EditPlan, int, int], str]] = {
    "crop": crop_reframe,
    "blur": blur_reframe,
    "pad": pad_reframe,
    "smart": smart_reframe,
}


# --- graph ---------------------------------------------------------------------------


def _punch_in(cut: Cut, w: int, h: int, fps: int) -> str:
    """Smooth zoom from 1.0 to ``cut.zoom`` over the cut."""
    frames = max(round(cut.out_duration * fps), 1)
    return (
        f"zoompan=z='1+{cut.zoom - 1:.4f}*min(on/{frames},1)':x='iw/2-iw/zoom/2'"
        f":y='ih/2-ih/zoom/2':d=1:s={w}x{h}:fps={fps}"
    )


def _video_chain(i: int, cut: Cut, plan: EditPlan, s: MontageSettings, mode: str) -> str:
    w, h, fps = s.width, s.height, s.fps
    speed = f"/{cut.speed:g}" if cut.speed != 1.0 else ""
    frame = REFRAMES[mode](f"c{i}", cut, plan, w, h)
    chain = f"[{i}:v]setpts=(PTS-STARTPTS){speed},fps={fps},{frame}"
    if cut.zoom > 1.0:
        chain += "," + _punch_in(cut, w, h, fps)
    return chain + f",setsar=1,format=yuv420p[v{i}]"


def _loop_still(i: int, plan: EditPlan, s: MontageSettings, mode: str) -> str:
    """The opening frame (reframed like the first cut), held for ``plan.loop_s``."""
    frames = max(round(plan.loop_s * s.fps), 1)
    frame = REFRAMES[mode]("lp", plan.cuts[0], plan, s.width, s.height)
    return (
        f"[{i}:v]setpts=PTS-STARTPTS,fps={s.fps},{frame},setsar=1,format=yuv420p,"
        f"trim=end_frame=1,loop=loop={frames - 1}:size=1,setpts=N/({s.fps}*TB),settb=AVTB[vfirst]"
    )


def _audio_chain(i: int, cut: Cut, has_audio: bool) -> str:
    d = cut.out_duration
    if not has_audio:
        return f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.3f}[a{i}]"
    tempo = f",atempo={cut.speed:g}" if cut.speed != 1.0 else ""
    edge = AUDIO_EDGE_S
    return (
        f"[{i}:a]asetpts=PTS-STARTPTS{tempo},aresample=48000,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={d:.3f},"
        f"atrim=duration={d:.3f},afade=t=in:d={edge},"
        f"afade=t=out:st={max(d - edge, 0):.3f}:d={edge}[a{i}]"
    )


def _accent_hex(ass_colour: str) -> str:
    """ASS ``&HAABBGGRR`` → ffmpeg ``0xRRGGBB``."""
    bgr = ass_colour.strip("&Hh")[-6:]
    return "0x" + f"{bgr[4:6]}{bgr[2:4]}{bgr[0:2]}".upper()


def build_filter_graph(
    plan: EditPlan, settings: MontageSettings, ass_path: str | None = None
) -> str:
    """Build the full filter graph.

    Inputs ``0..N-1`` are the cuts, then the opening still (when ``plan.loop_s``), then the
    music (if any). Output pads are ``[vout]`` and ``[aout]``.
    """
    s, d = settings, plan.duration
    mode = plan.reframe or s.reframe
    n = len(plan.cuts)
    loop = _loops(plan)
    parts = [_video_chain(i, c, plan, s, mode) for i, c in enumerate(plan.cuts)]
    parts += [_audio_chain(i, c, plan.source.has_audio) for i, c in enumerate(plan.cuts)]
    pads = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{pads}concat=n={n}:v=1:a=1[vc][ac]")
    if loop:  # crossfade the tail into the opening frame so the replay is seamless
        parts.append(_loop_still(n, plan, s, mode))
        parts.append(
            f"[vc]settb=AVTB[vct];[vct][vfirst]xfade=transition=fade:duration={plan.loop_s:g}"
            f":offset={d - plan.loop_s:.3f}[vl]"
        )

    video = ["[vl]" if loop else "[vc]"]
    if s.color_grade:
        video.append(GRADE)
    if ass_path:
        video.append(f"ass={escape_filter_path(ass_path)}")
    fade_out = max(d - s.fade_s, 0)
    if s.fade_s > 0 and not loop:  # a loop must not dip to black
        video.append(f"fade=t=in:st=0:d={s.fade_s},fade=t=out:st={fade_out:.3f}:d={s.fade_s}")
    vlabel = "[vbar]" if s.progress_bar else "[vout]"
    parts.append(video[0] + (",".join(video[1:]) or "null") + vlabel)
    if s.progress_bar:
        bar_h = max(4, s.height // 240)
        parts.append(
            f"color=c={_accent_hex(plan.variation.accent)}@0.9:s={s.width}x{bar_h}:r={s.fps}:d={d:.3f}[bar];"
            f"[vbar][bar]overlay=x='-w+W*t/{d:.3f}':y=H-{bar_h}:eval=frame:eof_action=pass,format=yuv420p[vout]"
        )

    loud = f"loudnorm=I={s.loudness_lufs:g}:TP=-1.5:LRA=11,aresample=48000"
    if loop:  # audio from frame 0; tail eases out under the visual crossfade
        fades = (
            f"afade=t=in:d={AUDIO_EDGE_S},afade=t=out:st={d - plan.loop_s:.3f}:d={plan.loop_s:g}"
        )
    elif s.fade_s > 0:
        fades = f"afade=t=in:d={s.fade_s},afade=t=out:st={fade_out:.3f}:d={s.fade_s}"
    else:
        fades = "anull"
    music_in = n + 1 if loop else n
    if plan.music:
        parts.append(
            f"[ac]asplit=2[voice][sc];"
            f"[{music_in}:a]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={s.music_volume_db:g}dB[mus];"
            f"[mus][sc]{DUCK}[duck];"
            f"[voice][duck]amix=inputs=2:duration=first:normalize=0,{loud},{fades}[aout]"
        )
    else:
        parts.append(f"[ac]{loud},{fades}[aout]")
    return ";".join(parts)


def _loops(plan: EditPlan) -> bool:
    return plan.loop_s > 0 and plan.duration > 2 * plan.loop_s


def build_command(
    plan: EditPlan, dst: str, settings: MontageSettings, ass_path: str | None = None
) -> list[str]:
    """Full ffmpeg argv: one fast-seeked input per cut, optional looped music, H.264/AAC out."""
    s = settings
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error"]
    for c in plan.cuts:
        cmd += ["-ss", f"{c.start:.3f}", "-t", f"{c.end - c.start:.3f}", "-i", plan.source.path]
    if _loops(plan):
        cmd += ["-ss", f"{plan.cuts[0].start:.3f}", "-t", "0.5", "-i", plan.source.path]
    if plan.music:
        cmd += ["-stream_loop", "-1", "-i", plan.music]
    cmd += [
        "-filter_complex", build_filter_graph(plan, s, ass_path),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-profile:v", "high", "-preset", s.preset, "-crf", str(s.crf),
        "-pix_fmt", "yuv420p", "-r", str(s.fps), "-g", str(2 * s.fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{plan.duration:.3f}", "-movflags", "+faststart", dst,
    ]  # fmt: skip
    return cmd
