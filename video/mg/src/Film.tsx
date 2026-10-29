import React from 'react';
import {AbsoluteFill, Audio, Freeze, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {Title} from './Title';
import {Beat} from './Beat';
import {Lines} from './Lines';
import {Count} from './Count';
import {Punch} from './Punch';
import {BuiltWith, Caption, CropGrid, EmailCard, EndCard, Inspect, PanelFrame, Tag, Windows} from './FilmMG';
import {fontFamily, PAPER, RED} from './theme';

// 3:00 ad cut (video/film-v3.md). 24 fps, 4320 frames. Dark act 1 -> white product -> light morning.
// Frame numbers below are the brief's timeline; GEN = generated shot, REAL = our footage / site capture, MG = graphics.

export const FILM_FRAMES = 4320;

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// Narration: [file, start frame, length in seconds].
export const VO: [string, number, number][] = [
  ['L01', 300, 3.92], ['L02', 412, 5.44], ['L03', 544, 2.32], ['L04', 736, 1.68], ['L05', 1140, 7.68],
  ['L06', 1572, 4.96], ['L07', 1932, 11.76], ['L08', 2772, 2.56], ['L09', 2964, 3.04], ['L10', 3126, 6.16],
  ['L11', 3318, 2.40], ['L12', 3760, 4.40],
];

const QUIET: [number, number] = [2520, 2664];

// Music: 0.25 under the dark act, 0.8 from the drop, ducked ~0.56x under narration (6-frame ramps),
// pulled to ~0.03 for the silent hit on the open door.
const musicVolume = (f: number) => {
  const base = f < 600 ? 0.25 : 0.8;
  let duck = 0;
  for (const [, s, sec] of VO) {
    const e = s + Math.ceil(sec * 24);
    duck = Math.max(duck, interpolate(f, [s - 6, s, e, e + 6], [0, 1, 1, 0], clamp));
  }
  const quiet = interpolate(f, [QUIET[0] - 6, QUIET[0], QUIET[1], QUIET[1] + 6], [0, 1, 1, 0], clamp);
  const v = base * (1 - 0.44 * duck);
  return v + (0.03 - v) * quiet;
};

const Sfx: React.FC<{at: number; src: string; vol?: number; len?: number}> = ({at, src, vol = 0.6, len = 96}) => (
  <Sequence from={at} durationInFrames={len} layout="none">
    <Audio src={staticFile(`audio/${src}.mp3`)} volume={vol} />
  </Sequence>
);

// Generated shot: 1280x720 filled to 1920x1080, its own SFX kept.
const Gen: React.FC<{n: number; vol: number; rate?: number}> = ({n, vol, rate = 1}) => (
  <AbsoluteFill style={{background: '#000'}}>
    <OffthreadVideo src={staticFile(`gen/GEN${n}.mp4`)} volume={vol} playbackRate={rate}
      style={{width: '100%', height: '100%', objectFit: 'cover'}} />
  </AbsoluteFill>
);

const WALK = {src: 'real/overlay_walk.mp4', w: 1080, h: 1920};
const LIVE = {src: 'cap/live_run.mp4', w: 1920, h: 1080};
const TOUR = {src: 'cap/result_tour.mp4', w: 1920, h: 1080};

// TRACK montage: [source s, length frames, cx, cy, scale] — moments the boxes are locked on.
const TRACK_CUTS: [number, number, number, number, number][] = [
  [0.5, 24, 180, 1200, 2.4], [4.6, 24, 300, 1000, 2.2], [11.8, 30, 650, 750, 2.0], [17.6, 24, 420, 900, 2.0],
  [23.6, 30, 450, 600, 1.9], [35.5, 24, 560, 900, 2.0], [39.5, 30, 560, 860, 1.9], [47.5, 24, 450, 900, 1.9],
  [59.5, 36, 560, 760, 2.4], [73.5, 42, 560, 500, 2.0],
];
const TRACK_AT = 1128;

// Result page, fast: tiles -> checklist -> timestamp chip click -> the video jumps to the door.
const TOUR_CUTS: [number, number, number, number, number][] = [
  [2.2, 30, 620, 300, 1.8], [3.3, 41, 1280, 420, 1.6], [5.2, 38, 1000, 400, 1.5], [6.6, 83, 673, 360, 1.8],
];
const TOUR_AT = 2760;
const CHIP_CLICK = 22; // frames into cut 3 (source 6.1 s)

const NAME_CHIP = (i: number) => 70 + i * 16;
const TOOL_CHIP = (i: number) => 8 + i * 10;

const Stamp: React.FC = () => {
  const frame = useCurrentFrame();
  const a = interpolate(frame, [10, 16], [0, 1], clamp);
  return (
    <div style={{position: 'absolute', left: 80, bottom: 80, display: 'flex', alignItems: 'center', gap: 22,
      background: 'rgba(11,11,12,.82)', padding: '18px 30px', borderRadius: 18, opacity: a,
      transform: `translateY(${20 * (1 - a)}px)`}}>
      <span style={{width: 22, height: 22, borderRadius: 11, background: RED}} />
      <span style={{fontFamily: 'Menlo, monospace', fontSize: 84, fontWeight: 600, color: PAPER}}>00:45</span>
    </div>
  );
};

const ChipRing: React.FC<{at: number}> = ({at}) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame - at, [-4, 0, 14], [0, 1, 0], clamp);
  return <div style={{position: 'absolute', left: 1076, top: 340, width: 72, height: 36, borderRadius: 18,
    border: `4px solid #1f9d57`, opacity: t, transform: `scale(${1 + 0.3 * (1 - t)})`}} />;
};

const Morning: React.FC = () => {
  const frame = useCurrentFrame();
  const b = interpolate(frame, [0, 144], [1, 1.45], clamp);
  const s = interpolate(frame, [0, 144], [1, 1.04], clamp);
  return (
    <AbsoluteFill style={{filter: `brightness(${b})`, transform: `scale(${s})`}}>
      <Freeze frame={143}><Gen n={5} vol={0} /></Freeze>
    </AbsoluteFill>
  );
};

export const Film: React.FC = () => {
  let t = TRACK_AT;
  let r = TOUR_AT;
  return (
    <AbsoluteFill style={{background: '#000', fontFamily}}>
      {/* ACT 1 — dark */}
      <Sequence from={12} durationInFrames={121}><Gen n={1} vol={1} /></Sequence>
      <Sequence from={133} durationInFrames={156}>
        <Lines lines={['Every building', 'has a door', 'that should be closed.']} every={52} />
      </Sequence>
      <Sequence from={289} durationInFrames={121}><Gen n={2} vol={1} /></Sequence>
      <Sequence from={410} durationInFrames={130}>
        <Gen n={3} vol={1} rate={0.75} />
        <Caption text="Fire Facilities Act · Art. 22" delay={4} />
      </Sequence>

      {/* ACT 2 — drop to white */}
      <Sequence from={600} durationInFrames={120}><Title /></Sequence>
      <Sequence from={720} durationInFrames={121}><Gen n={4} vol={0.6} /></Sequence>
      <Sequence from={841} durationInFrames={120}>
        <PanelFrame label="4×">
          <Punch {...WALK} from={0} rate={4} cx={540} cy={960} s0={0.5} dur={120} boxW={540} boxH={960} />
        </PanelFrame>
      </Sequence>
      {/* live site: drop -> Run inspection (real time, passcode blurred) -> progress at 40x */}
      <Sequence from={961} durationInFrames={43}>
        <Punch {...LIVE} from={5.6} cx={1150} cy={665} s0={1.3} s1={1.4} dur={43}>
          <div style={{position: 'absolute', left: 420, top: 503, width: 320, height: 74, borderRadius: 12,
            backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', background: 'rgba(255,255,255,.35)'}} />
        </Punch>
      </Sequence>
      <Sequence from={1004} durationInFrames={24}>
        <Punch {...LIVE} from={8} rate={40} cx={680} cy={460} s0={1.5} s1={1.6} dur={24} /><Tag text="sped up · 40×" />
      </Sequence>
      <Sequence from={1028} durationInFrames={24}>
        <Punch {...LIVE} from={190} rate={40} cx={680} cy={500} s0={1.5} s1={1.6} dur={24} /><Tag text="sped up · 40×" />
      </Sequence>
      <Sequence from={1052} durationInFrames={28}>
        <Punch {...LIVE} from={360} rate={30} cx={680} cy={720} s0={1.5} s1={1.6} dur={28} /><Tag text="sped up · 30×" />
      </Sequence>

      {/* ACT 3 — TRACK */}
      <Sequence from={1080} durationInFrames={48}><Beat word="TRACK" sub="SAM 3.1" /></Sequence>
      {TRACK_CUTS.map(([src, len, cx, cy, s], i) => {
        const at = t; t += len;
        return (
          <Sequence key={i} from={at} durationInFrames={len}>
            <Punch {...WALK} from={src} cx={cx} cy={cy} s0={s} s1={s * 1.08} dur={len} />
          </Sequence>
        );
      })}
      <Sequence from={1416} durationInFrames={96}><Windows /></Sequence>

      {/* NAME */}
      <Sequence from={1512} durationInFrames={48}><Beat word="NAME" sub="Muse Spark" /></Sequence>
      <Sequence from={1560} durationInFrames={312}><CropGrid chipAt={NAME_CHIP} /></Sequence>

      {/* INSPECT */}
      <Sequence from={1872} durationInFrames={48}><Beat word="INSPECT" sub="Muse Spark + tools" /></Sequence>
      <Sequence from={1920} durationInFrames={600}><Inspect chipAt={TOOL_CHIP} /></Sequence>

      {/* ACT 4 — found */}
      <Sequence from={2520} durationInFrames={144}>
        <Punch {...WALK} from={44.5} freeze cx={724} cy={845} s0={1.78} s1={2.15} dur={144} />
        <Stamp />
      </Sequence>
      <Sequence from={2664} durationInFrames={48}>
        <Punch {...WALK} from={131.9} cx={760} cy={1000} s0={2.2} s1={2.4} dur={48} />
      </Sequence>
      <Sequence from={2712} durationInFrames={48}>
        <Punch {...WALK} from={134.0} freeze cx={760} cy={1000} s0={2.4} s1={2.6} dur={48} />
      </Sequence>
      {TOUR_CUTS.map(([src, len, cx, cy, s], i) => {
        const at = r; r += len;
        return (
          <Sequence key={i} from={at} durationInFrames={len}>
            <Punch {...TOUR} from={src} cx={cx} cy={cy} s0={s} s1={s * 1.06} dur={len}>
              {i === 2 && <ChipRing at={CHIP_CLICK} />}
            </Punch>
          </Sequence>
        );
      })}
      <Sequence from={2952} durationInFrames={168}><EmailCard cps={1.75} /></Sequence>
      <Sequence from={3120} durationInFrames={192}><Count /></Sequence>
      <Sequence from={3312} durationInFrames={144}><Gen n={5} vol={0.6} /></Sequence>
      <Sequence from={3456} durationInFrames={144}><Morning /></Sequence>

      {/* ACT 5 — close */}
      <Sequence from={3600} durationInFrames={144}><BuiltWith /></Sequence>
      <Sequence from={3744} durationInFrames={145}><Gen n={6} vol={0.6} /></Sequence>
      <Sequence from={3889} durationInFrames={431}><EndCard /></Sequence>

      {/* ── audio ── */}
      <Audio src={staticFile('audio/score.mp3')} volume={musicVolume} />
      {VO.map(([f, at, sec]) => (
        <Sequence key={f} from={at} durationInFrames={Math.ceil(sec * 24) + 2} layout="none">
          <Audio src={staticFile(`audio/${f}.mp3`)} volume={1} />
        </Sequence>
      ))}
      <Sfx at={504} src="riser" vol={0.7} />
      <Sfx at={600} src="impact" vol={0.45} />
      <Sfx at={836} src="whoosh" vol={0.5} len={29} />
      <Sfx at={956} src="whoosh" vol={0.4} len={29} />
      <Sfx at={997} src="click" vol={0.9} len={15} />
      {[1004, 1012, 1020, 1028, 1036, 1044, 1052, 1060, 1068].map((f) => <Sfx key={f} at={f} src="tick" vol={0.25} len={12} />)}
      {[1076, 1508, 1868].map((f) => <Sfx key={f} at={f} src="whoosh" vol={0.45} len={29} />)}
      {TRACK_CUTS.map((_, i) => <Sfx key={i} at={TRACK_AT + TRACK_CUTS.slice(0, i).reduce((a, c) => a + c[1], 0)}
        src="tick" vol={0.3} len={12} />)}
      <Sfx at={1414} src="whoosh" vol={0.35} len={29} />
      {Array.from({length: 11}, (_, i) => <Sfx key={i} at={1560 + NAME_CHIP(i)} src="tick" vol={0.35} len={12} />)}
      {Array.from({length: 14}, (_, i) => <Sfx key={i} at={1920 + TOOL_CHIP(i)} src="tick" vol={0.35} len={12} />)}
      {[1920 + 482, 1920 + 506].map((f) => <Sfx key={f} at={f} src="click" vol={0.6} len={15} />)}
      <Sfx at={2520} src="impact" vol={1} />
      <Sfx at={2660} src="whoosh" vol={0.8} len={29} />
      {TOUR_CUTS.map((_, i) => <Sfx key={i} at={TOUR_AT + TOUR_CUTS.slice(0, i).reduce((a, c) => a + c[1], 0)}
        src="tick" vol={0.3} len={12} />)}
      <Sfx at={TOUR_AT + 30 + 41 + CHIP_CLICK} src="click" vol={0.8} len={15} />
      <Sfx at={2948} src="whoosh" vol={0.35} len={29} />
      {Array.from({length: 35}, (_, i) => <Sfx key={i} at={2962 + i * 4} src="tick" vol={0.15} len={8} />)}
      {Array.from({length: 10}, (_, i) => <Sfx key={i} at={3160 + i * 6} src="tick" vol={0.2} len={8} />)}
    </AbsoluteFill>
  );
};

// Narration only — rendered to audio to check where each line lands (silencedetect).
export const FilmVO: React.FC = () => (
  <AbsoluteFill>
    {VO.map(([f, at, sec]) => (
      <Sequence key={f} from={at} durationInFrames={Math.ceil(sec * 24) + 2} layout="none">
        <Audio src={staticFile(`audio/${f}.mp3`)} volume={1} />
      </Sequence>
    ))}
  </AbsoluteFill>
);
