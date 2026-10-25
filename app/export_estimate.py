"""Pure, approximate export planning; no model, device, files, or user settings.

Units approximate spoken syllables: one CJK character/digit, and roughly one
unit per three Latin letters (at least one per word). These are neither tokens
nor an exact pronunciation model. Timing ranges are planning heuristics, not
confidence intervals or a guarantee. Calibration must measure original model
audio and generation time before tempo adjustment, playback, or MP3 writing.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata


MAX_CHARACTERS = 1_000_000
MAX_SAMPLE_CHARACTERS = 320
_LATIN = r"A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"  # Excludes multiplication/division symbols.
_WORDS = re.compile(rf"[{_LATIN}]+(?:['’][{_LATIN}]+)*|[^{_LATIN}]", re.DOTALL)
_SENTENCE_END = re.compile(r"[。！？!?；;\n]+|\.(?=\s|$)")


@dataclass(frozen=True)
class TextAnalysis:
    characters: int
    units: float
    segments: int
    english_fraction: float

    @property
    def language_category(self) -> str:
        """Coarse cache key only; numerical mixture still widens uncertainty."""
        if self.english_fraction <= .15:
            return "zh"
        if self.english_fraction >= .85:
            return "en"
        return "mixed"


@dataclass(frozen=True)
class Sample:
    text: str
    analysis: TextAnalysis
    parts: tuple[str, ...]


@dataclass(frozen=True)
class EstimateRange:
    lower: float
    upper: float


@dataclass(frozen=True)
class Calibration:
    analysis: TextAnalysis
    generation_seconds: float
    audio_seconds: float

    def __post_init__(self):
        _validate_analysis(self.analysis)
        for value in (self.generation_seconds, self.audio_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("校准耗时和音频时长必须为有限正数。")


@dataclass(frozen=True)
class ExportEstimate:
    total_seconds: EstimateRange | None
    audio_seconds: EstimateRange
    generation_seconds: EstimateRange | None
    disk_bytes_upper: int
    calibrated: bool


def _normalized(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("请输入文字。")
    # Match the Reader's input normalization and accepted export size.
    result = re.sub(r"[\t\r ]+", " ", text).strip()
    if len(result) > MAX_CHARACTERS:
        raise ValueError("一次最多处理 1000000 字，请分次选择。")
    return result


def _units(text: str) -> tuple[float, float]:
    total = english = 0.0
    for match in _WORDS.finditer(text):
        word = match.group()
        if len(word) > 1 or (word and "LATIN" in unicodedata.name(word[0], "")):
            letters = sum(char.isalpha() for char in word)
            count = max(1.0, letters / 3.0)
            total += count
            english += count
        elif word.isalnum():
            total += 1.0
    return total, english


def _parts(text: str) -> tuple[str, ...]:
    """Approximate segment count; callers should pass actual Reader parts when available."""
    parts = []
    start = 0
    while start < len(text):
        window = text[start:start + 300]
        candidates = [m.end() for m in re.finditer(r"[。！？!?；;\n]", window)]
        cut = candidates[-1] if len(window) == 300 and candidates and candidates[-1] >= 100 else len(window)
        if part := window[:cut].strip():
            parts.append(part)
        start += cut
    return tuple(parts)


def analyze_text(text: str, parts=None) -> TextAnalysis:
    """Count normalized characters, approximate spoken units, and actual calls.

    Pass the Reader's parts when available so segment overhead follows its
    actual input. Punctuation and whitespace do not count as spoken units.
    """
    text = _normalized(text)
    total, english = _units(text)
    if total <= 0:
        raise ValueError("没有可朗读的文字。")
    if parts is None:
        segments = len(_parts(text))
    else:
        parts = tuple(parts)
        if not parts or any(not isinstance(part, str) or not part.strip() for part in parts):
            raise ValueError("分段必须包含非空文字。")
        if re.sub(r"\s", "", "".join(parts)) != re.sub(r"\s", "", text):
            raise ValueError("分段与待估算文字不一致。")
        segments = len(parts)
    return TextAnalysis(len(text), total, segments, english / total)


def _bounded_prefix(text: str, max_units: float) -> str:
    # The character cap applies even to long words, symbols, or punctuation.
    bounded = text[:MAX_SAMPLE_CHARACTERS]
    count = 0.0
    end = 0
    for match in _WORDS.finditer(bounded):
        value = _units(match.group())[0]
        if count + value > max_units:
            if count < min(30, max_units * .6) and len(match.group()) > 1:
                # A long unbroken Latin word cannot become an unbounded probe.
                end = match.start() + min(len(match.group()), max(1, int((max_units - count) * 3)))
            break
        count += value
        end = match.end()
    prefix = bounded[:end].strip()
    # Prefer a clause boundary when a full sentence cannot fit, provided this
    # does not reduce a useful probe to a tiny introductory fragment.
    clauses = list(re.finditer(r"[,，:：、]\s*", prefix))
    for boundary in reversed(clauses):
        clause = prefix[:boundary.end()].strip()
        if _units(clause)[0] >= min(30, max_units * .6):
            return clause
    return prefix


def select_sample(text: str, parts=None, target_units: float = 60, max_units: float = 80) -> Sample:
    """Select a short contiguous opening sample, favoring complete sentences.

    The sample is at most 80 approximate units by default and always at most
    320 characters. A long first sentence is cut at a substantial clause or
    a word/character boundary. Short inputs are sampled in full.
    """
    analyze_text(text, parts=parts)
    if not (math.isfinite(target_units) and math.isfinite(max_units)
            and 1 <= target_units <= max_units <= 80):
        raise ValueError("样本单位必须满足 1 <= target <= max <= 80。")
    text = _normalized(text)
    selected = ""
    # Yield sentence ends lazily; even a million-character first sentence is
    # inspected only once and never analyzed in every growing prefix.
    def sentences():
        start = 0
        for boundary in _SENTENCE_END.finditer(text):
            yield text[start:boundary.end()]
            start = boundary.end()
        if start < len(text):
            yield text[start:]

    for sentence in sentences():
        candidate = selected + sentence
        if len(candidate) > MAX_SAMPLE_CHARACTERS or _units(candidate)[0] > max_units:
            if not selected or _units(selected)[0] < min(30, target_units / 2):
                candidate = _bounded_prefix(candidate, max_units)
                if _units(candidate)[0] > 0:
                    selected = candidate
            break
        selected = candidate
        if _units(selected)[0] >= target_units:
            break
    selected = selected.strip()
    # Leading punctuation can exhaust the bounded prefix without speech.
    if not selected or _units(selected)[0] <= 0:
        first = next(match.start() for match in re.finditer(r"\w", text) if match.group().isalnum())
        selected = _bounded_prefix(text[first:], max_units)
    sample_parts = (selected,)
    return Sample(selected, analyze_text(selected, sample_parts), sample_parts)


def _validate_analysis(analysis: TextAnalysis):
    if (not isinstance(analysis, TextAnalysis)
            or not 1 <= analysis.characters <= MAX_CHARACTERS
            or not math.isfinite(analysis.units) or analysis.units <= 0
            or not 1 <= analysis.segments <= analysis.characters
            or not math.isfinite(analysis.english_fraction)
            or not 0 <= analysis.english_fraction <= 1):
        raise ValueError("文字分析数据无效。")


def estimate_export(analysis: TextAnalysis, calibration: Calibration | None = None,
                    speed: float = 1.0) -> ExportEstimate:
    """Plan warmed export time; model loading/waiting/probe time is excluded.

    A single probe cannot identify a fixed per-call overhead. Bounds therefore
    bracket plausible overhead, penalize very small probes, long extrapolation
    and language mismatch. Changing speed affects duration and encoding costs,
    never the measured model generation rate. Without calibration, no machine
    generation/export time is invented.

    Disk provision covers both temporary MP3 and final MP3 at 320 kbit/s plus
    headers/slack, conditional on the estimated audio upper duration. It is a
    conservative planning amount, not a guaranteed free-space requirement.
    """
    _validate_analysis(analysis)
    if not math.isfinite(speed) or not .75 <= speed <= 1.5:
        raise ValueError("语速必须在 0.75–1.50 之间。")
    generation = total = None
    if calibration is None:
        # A broad speech-rate heuristic, with modest inter-segment pauses.
        audio = EstimateRange(analysis.units / 6 / speed,
                              (analysis.units / 2 + analysis.segments) / speed)
    else:
        sample = calibration.analysis
        _validate_analysis(sample)
        ratio = analysis.units / sample.units
        mixture = abs(analysis.english_fraction - sample.english_fraction)
        extrapolation = min(.35, max(0, math.log10(max(1, ratio))) * .1)
        short_probe = max(0, 40 - sample.units) / 40 * .3
        uncertainty = min(.8, .2 + extrapolation + short_probe + mixture * .5)
        raw_audio = calibration.audio_seconds * ratio
        audio = EstimateRange(raw_audio * (1 - uncertainty) / speed,
                              (raw_audio * (1 + uncertainty) + analysis.segments * .5) / speed)
        # At most 30% of the probe is treated as startup overhead. One probe
        # cannot determine its exact share; include both endpoint models.
        overhead = min(2.0, calibration.generation_seconds * .3) / sample.segments
        variable = max(0, calibration.generation_seconds - overhead * sample.segments)
        with_overhead = variable * ratio + overhead * analysis.segments
        proportional = calibration.generation_seconds * ratio
        generation = EstimateRange(min(with_overhead, proportional) * (1 - uncertainty),
                                   (max(with_overhead, proportional) + overhead * analysis.segments) * (1 + uncertainty))
        # Encoding/copy allowance is heuristic and deliberately independent of
        # the measured model rate. It is not a disk throughput benchmark.
        total = EstimateRange(generation.lower + audio.lower * .002,
                              generation.upper + audio.upper * .05 + 2)
    disk = math.ceil((audio.upper * 40_000 + 65_536) * 2 * 1.1)
    return ExportEstimate(total, audio, generation, disk, calibration is not None)
