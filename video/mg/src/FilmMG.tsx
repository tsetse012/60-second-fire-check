import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {fontFamily, GREEN, INK, PAPER, RED} from './theme';
import data from '../public/data.json';

// Motion graphics for the 3-minute film. Same language as Title/Beat/Count: Inter, white paper, black heavy type,
// green = a box the tracker locked, red = a problem. Every string on screen comes from the brief or data.json.

const MONO = 'Menlo, "SF Mono", monospace';
const GREY = '#6b6b70';
const AMBER = '#c98a12';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// Small caption that snaps on (Act 1 legal line).
export const Caption: React.FC<{text: string; delay?: number}> = ({text, delay = 0}) => {
  const frame = useCurrentFrame();
  const a = interpolate(frame - delay, [0, 6], [0, 1], clamp);
  return (
    <div style={{position: 'absolute', left: 90, bottom: 80, fontFamily, fontSize: 34, fontWeight: 600,
      color: '#fff', letterSpacing: 1, opacity: a, transform: `translateX(${-20 * (1 - a)}px)`,
      borderLeft: `4px solid ${RED}`, paddingLeft: 18, textShadow: '0 2px 12px rgba(0,0,0,.6)'}}>{text}</div>
  );
};

// Honest speed label for sped-up real footage.
export const Tag: React.FC<{text: string}> = ({text}) => (
  <div style={{position: 'absolute', right: 48, top: 40, fontFamily, fontSize: 26, fontWeight: 600, color: PAPER,
    background: 'rgba(11,11,12,.78)', padding: '6px 14px', borderRadius: 8, letterSpacing: 1}}>{text}</div>
);

// The overlay walk at 4x in a rounded portrait panel on white, with the speed written beside it.
export const PanelFrame: React.FC<{children: React.ReactNode; label: string}> = ({children, label}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 15, stiffness: 180}});
  return (
    <AbsoluteFill style={{background: PAPER, justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{width: 540, height: 960, borderRadius: 36, overflow: 'hidden', position: 'relative',
        transform: `scale(${0.85 + 0.15 * pop}) translateY(${40 * (1 - pop)}px)`,
        boxShadow: '0 30px 80px rgba(0,0,0,.18)'}}>{children}</div>
      <div style={{position: 'absolute', left: 1290, top: 470, fontSize: 120, fontWeight: 800, color: INK,
        letterSpacing: -4, opacity: interpolate(frame, [6, 14], [0, 1], clamp)}}>{label}</div>
    </AbsoluteFill>
  );
};

// One label that lands on a beat, holds, then lifts away for the next one.
const WinLabel: React.FC<{at: number; until: number; text: string}> = ({at, until, text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame: frame - at, fps, config: {damping: 15, stiffness: 200, mass: 0.7}});
  const out = interpolate(frame, [until, until + 4], [0, 1], {...clamp, easing: Easing.in(Easing.cubic)});
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: 370, textAlign: 'center', fontSize: 82, fontWeight: 800,
      color: INK, letterSpacing: -2.5, opacity: Math.min(1, pop * 1.6) * (1 - out),
      transform: `translateY(${26 * (1 - pop) - 22 * out}px)`}}>{text}</div>
  );
};

// End of TRACK: 15 real frames of the walk on top, and the 144.9 s timeline under them splitting into 10-second
// windows — each window gets a green detection box, matching boxes are joined across every seam, then it is one bar.
const WIN_N = 15;
const WIN_TOTAL = 144.9;
const BAR_W = 1760;
const BAR_H = 92;
const OBJ_W = 26;
const OBJ_H = 40;

export const Windows: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const draw = interpolate(frame, [0, 12], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const split = interpolate(frame, [12, 24], [0, 1], {...clamp, easing: Easing.out(Easing.back(1.1))});
  const boxes = interpolate(frame, [24, 34], [0, 1], clamp);
  const bridge = interpolate(frame, [34, 44], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const join = interpolate(frame, [44, 50], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const green = interpolate(frame, [38, 46], [0, 1], clamp);
  const gap = 14 * split * (1 - join);
  const usable = BAR_W - gap * (WIN_N - 1);
  let x = 0;
  const segs = Array.from({length: WIN_N}, (_, i) => {
    const len = Math.min(10, WIN_TOTAL - i * 10);
    const sw = (usable * len) / WIN_TOTAL;
    const seg = {x, sw, i};
    x += sw + gap;
    return seg;
  });
  const barColor = `color-mix(in srgb, ${GREEN} ${green * 100}%, ${INK})`;
  // The same object sits on both sides of a seam; its height drifts a little from seam to seam.
  const seamY = (k: number) => BAR_H / 2 + 16 * Math.sin(k * 1.9);
  return (
    <AbsoluteFill style={{background: PAPER, fontFamily}}>
      <div style={{position: 'absolute', left: 80, top: 300, width: BAR_W, height: 471}}>
        {/* the walk's real frames, one per window */}
        {segs.map(({x, sw, i}) => {
          const pop = spring({frame: frame - 2 - i * 1.5, fps, config: {damping: 15, stiffness: 200}});
          return (
            <div key={`t${i}`} style={{position: 'absolute', left: x, top: 0, width: sw, height: 185,
              borderRadius: 8, overflow: 'hidden', background: '#ececee', opacity: Math.min(1, pop * 1.6),
              transform: `translateY(${18 * (1 - pop)}px)`}}>
              <Img src={staticFile(`windows/w${String(i).padStart(2, '0')}.jpg`)}
                style={{width: '100%', height: '100%', objectFit: 'cover'}} />
            </div>
          );
        })}
        {/* the timeline bar */}
        <div style={{position: 'absolute', left: 0, top: 205, width: BAR_W, height: BAR_H,
          clipPath: `inset(-200px ${100 - draw * 100}% -200px 0)`}}>
          {segs.map(({x, sw, i}) => (
            <div key={i} style={{position: 'absolute', left: x, top: 0, width: sw, height: BAR_H,
              borderRadius: 8 * split * (1 - join),
              background: barColor, outline: split > 0.02 ? `4px solid ${GREEN}` : 'none', outlineOffset: 0,
              boxShadow: `inset 0 0 0 4px rgba(255,255,255,${0.18 * boxes * (1 - join)})`}} />
          ))}
          {/* the object, boxed in both windows either side of a seam, then joined by a green line */}
          {boxes > 0 && segs.slice(0, -1).map(({x, sw}, k) => {
            const y = seamY(k) - OBJ_H / 2;
            const a = Math.min(1, boxes) * (1 - join);
            if (a <= 0) return null;
            return (
              <React.Fragment key={`o${k}`}>
                <div style={{position: 'absolute', left: x + sw - OBJ_W - 8, top: y, width: OBJ_W, height: OBJ_H,
                  border: `4px solid ${GREEN}`, borderRadius: 5, background: 'rgba(255,255,255,.14)', opacity: a}} />
                <div style={{position: 'absolute', left: x + sw + gap + 8, top: y, width: OBJ_W, height: OBJ_H,
                  border: `4px solid ${GREEN}`, borderRadius: 5, background: 'rgba(255,255,255,.14)', opacity: a}} />
                <div style={{position: 'absolute', left: x + sw - 4, top: seamY(k) - 2.5,
                  width: gap + OBJ_W + 24, height: 5, background: GREEN, borderRadius: 3,
                  transform: `scaleX(${bridge})`, transformOrigin: 'left center', opacity: a * (1 - join)}} />
              </React.Fragment>
            );
          })}
        </div>
        <div style={{position: 'absolute', left: 0, top: 318, fontFamily: MONO, fontSize: 32, fontWeight: 600,
          color: GREY}}>0 s</div>
        <div style={{position: 'absolute', right: 0, top: 318, fontFamily: MONO, fontSize: 32, fontWeight: 600,
          color: GREY}}>144.9 s</div>
        <WinLabel at={0} until={20} text="16 objects per request" />
        <WinLabel at={24} until={42} text="10-second windows" />
        <WinLabel at={46} until={200} text="stitched" />
      </div>
    </AbsoluteFill>
  );
};

const labelColor = (label: string) => label === 'OBSTRUCTION' ? RED : label === 'NOT FIRE EQUIPMENT' ? GREY : GREEN;

// NAME: the run's real crops fly into a grid and Muse Spark's label types onto each.
export const CropGrid: React.FC<{chipAt: (i: number) => number}> = ({chipAt}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const cw = 270, ch = 340, gap = 26;
  return (
    <AbsoluteFill style={{background: PAPER, fontFamily, justifyContent: 'center', alignItems: 'center'}}>
      <div style={{display: 'flex', flexDirection: 'column', gap: 40, alignItems: 'center'}}>
        {[data.crops.slice(0, 6), data.crops.slice(6)].map((row, r) => (
          <div key={r} style={{display: 'flex', gap}}>
            {row.map((c, j) => {
              const i = r * 6 + j;
              const fly = spring({frame: frame - i * 5, fps, config: {damping: 14, stiffness: 170}});
              const dir = i % 2 ? 1 : -1;
              const typed = Math.max(0, Math.floor((frame - chipAt(i)) * 1.4));
              const text = c.label.slice(0, typed);
              const col = labelColor(c.label);
              const box = interpolate(frame - chipAt(i), [-6, 2], [0, 1], clamp);
              return (
                <div key={c.track} style={{width: cw, opacity: Math.min(1, fly * 1.4),
                  transform: `translate(${dir * 500 * (1 - fly)}px, ${(r ? 300 : -300) * (1 - fly)}px) rotate(${dir * 8 * (1 - fly)}deg)`}}>
                  <div style={{position: 'relative', width: cw, height: ch, borderRadius: 14, overflow: 'hidden',
                    background: '#eee'}}>
                    <Img src={staticFile(c.file)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
                    <div style={{position: 'absolute', inset: 8, border: `4px solid ${col}`, borderRadius: 8,
                      opacity: box, clipPath: `inset(0 ${100 - box * 100}% 0 0)`}} />
                  </div>
                  <div style={{marginTop: 14, height: 76, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 6}}>
                    {typed > 0 && <span style={{background: col, color: PAPER, fontSize: 20, fontWeight: 800,
                      padding: '6px 10px', borderRadius: 6, letterSpacing: 0.5, whiteSpace: 'nowrap'}}>{text}</span>}
                    {typed > c.label.length + 4 && <span style={{fontFamily: MONO, fontSize: 20, color: GREY}}>
                      {c.conf.toFixed(2)}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

// Status of the 17 checklist rows as the merged result reports them (result page: 2 issue · 4 check on site ·
// 9 ok · 2 not seen). Rows carry no rule text — only the shape of the checklist.
const ROWS: string[] = [RED, GREEN, RED, AMBER, GREY, GREY, GREEN, GREEN, AMBER, GREEN, GREEN, AMBER, GREEN,
  GREEN, AMBER, GREEN, GREEN];

const Row: React.FC<{i: number; at: number; w: number}> = ({i, at, w}) => {
  const frame = useCurrentFrame();
  const a = interpolate(frame - at, [0, 5], [0, 1], clamp);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: 16, height: 30, opacity: a,
      transform: `translateX(${24 * (1 - a)}px)`}}>
      <span style={{fontFamily: MONO, fontSize: 18, color: GREY, width: 28}}>{String(i + 1).padStart(2, '0')}</span>
      <span style={{flex: 'none', width: w * (0.45 + ((i * 37) % 40) / 100), height: 10, borderRadius: 5, background: '#e4e4e7'}} />
      <span style={{marginLeft: 'auto', width: 16, height: 16, borderRadius: 8, background: ROWS[i],
        transform: `scale(${interpolate(frame - at, [4, 9], [0, 1], clamp)})`}} />
    </div>
  );
};

const Lane: React.FC<{title: string; start: number; width: number}> = ({title, start, width}) => (
  <div style={{width, padding: '22px 28px', background: PAPER, borderRadius: 18, boxShadow: '0 12px 40px rgba(0,0,0,.08)',
    border: '1px solid #ececef'}}>
    <div style={{fontFamily: MONO, fontSize: 24, fontWeight: 600, color: INK, marginBottom: 12}}>{title}</div>
    {ROWS.map((_, i) => <Row key={i} i={i} at={start + i * 7} w={width - 160} />)}
  </div>
);

// INSPECT: tool calls fire as code chips -> two independent checks run side by side -> they merge into one
// 17-row checklist -> the door crop is asked "Open?" twice -> OPEN.
// Tool-call chips: two or three fire into the middle at once, then shrink and stack upwards as the next ones land.
// Type never drops below 34 px (40 px at 0.85), so every call stays legible.
const CHIP_H = 80;
const CHIP_FONT = 40;
const CHIP_GAP = 6;
const CHIP_BASE_Y = 940;

export const Inspect: React.FC<{chipAt: (i: number) => number}> = ({chipAt}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const chipsOut = interpolate(frame, [140, 156], [0, 1], clamp);
  const lanesIn = interpolate(frame, [152, 174], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const merge = interpolate(frame, [340, 372], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const listOut = interpolate(frame, [440, 456], [0, 1], clamp);
  const doorIn = spring({frame: frame - 452, fps, config: {damping: 15, stiffness: 160}});
  const open = spring({frame: frame - 532, fps, config: {damping: 9, stiffness: 260}});
  const asks = [482, 506];
  const chips = data.tools.map((t, i) => {
    const age = frame - chipAt(i);
    const entry = spring({frame: age, fps, config: {damping: 12, stiffness: 210, mass: 0.7}});
    const shrink = interpolate(age, [14, 44], [0, 1], clamp);
    return {t, i, on: age >= 0, entry,
      scale: (1 + 0.45 * (1 - entry)) * (1 - 0.15 * shrink), y: CHIP_BASE_Y, fade: 1};
  });
  const shown = chips.filter((c) => c.on);
  let acc = 0;
  for (const c of [...shown].reverse()) {
    c.y = CHIP_BASE_Y - acc;
    acc += CHIP_H * c.scale + CHIP_GAP;
    // Older calls keep sliding up and out of the frame instead of piling into the top edge.
    c.fade = interpolate(c.y - (CHIP_H * c.scale) / 2, [150, 260], [0, 1], clamp);
  }
  return (
    <AbsoluteFill style={{background: PAPER, fontFamily}}>
      {/* A — tool calls */}
      {chipsOut < 1 && (
        <AbsoluteFill style={{opacity: 1 - chipsOut, transform: `translateY(${-60 * chipsOut}px)`}}>
          {shown.map(({t, i, entry, scale, y, fade}) => {
            const submit = t.startsWith('submit');
            return (
              <span key={i} style={{position: 'absolute', left: 960, top: y - CHIP_H / 2, height: CHIP_H,
                display: 'inline-flex', alignItems: 'center', gap: 18, padding: '0 30px', borderRadius: 16,
                fontFamily: MONO, fontSize: CHIP_FONT, fontWeight: 600, whiteSpace: 'nowrap',
                color: submit ? PAPER : INK, background: submit ? GREEN : '#f2f2f4',
                border: `2px solid ${submit ? GREEN : '#dedee3'}`,
                boxShadow: `0 ${10 * Math.min(1, entry)}px ${26 * Math.min(1, entry)}px rgba(0,0,0,${0.1 * Math.min(1, entry)})`,
                opacity: Math.min(1, entry * 2) * fade,
                transform: `translateX(-50%) scale(${scale})`, transformOrigin: 'center center'}}>
                <span style={{width: 15, height: 15, borderRadius: 8, background: submit ? PAPER : GREEN}} />
                {t}
              </span>
            );
          })}
        </AbsoluteFill>
      )}
      {/* B/C — two lanes, then one checklist */}
      {lanesIn > 0 && listOut < 1 && (
        <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', opacity: lanesIn * (1 - listOut),
          transform: 'scale(1.3)'}}>
          <div style={{position: 'absolute', transform: `translateX(${-330 * (1 - merge)}px)`}}>
            <Lane title={merge > 0.5 ? 'check 1 + check 2' : 'check 1'} start={166} width={600} />
          </div>
          <div style={{position: 'absolute', transform: `translateX(${330 * (1 - merge)}px)`, opacity: 1 - merge}}>
            <Lane title="check 2" start={178} width={600} />
          </div>
        </AbsoluteFill>
      )}
      {/* D — the door, asked twice */}
      {frame >= 448 && (
        <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', flexDirection: 'row', gap: 110,
          opacity: Math.min(1, doorIn * 1.5)}}>
          <div style={{position: 'relative', height: 760, width: 760 * 335 / 768, borderRadius: 16,
            transform: `translateX(${-120 * (1 - doorIn)}px)`}}>
            <Img src={staticFile('crops/door_13.jpg')} style={{height: '100%', width: '100%', borderRadius: 16}} />
            <div style={{position: 'absolute', inset: -14, borderRadius: 12,
              border: `6px solid ${open > 0.02 ? RED : GREEN}`}} />
            <div style={{position: 'absolute', left: -14, top: -58, background: open > 0.02 ? RED : GREEN, color: PAPER,
              fontSize: 26, fontWeight: 800, padding: '6px 12px', borderRadius: 6}}>door_13</div>
          </div>
          <div style={{width: 700}}>
            {asks.map((at, k) => {
              const a = interpolate(frame - at, [0, 6], [0, 1], clamp);
              return (
                <div key={k} style={{display: 'flex', alignItems: 'baseline', gap: 26, marginBottom: 26, opacity: a,
                  transform: `translateY(${20 * (1 - a)}px)`}}>
                  <span style={{fontFamily: MONO, fontSize: 30, color: GREY, width: 150}}>{`check ${k + 1}`}</span>
                  <span style={{fontSize: 88, fontWeight: 800, color: INK, letterSpacing: -3}}>Open?</span>
                </div>
              );
            })}
            <div style={{fontSize: 210, fontWeight: 800, color: RED, letterSpacing: -9, marginTop: 10,
              opacity: Math.min(1, open * 2), transform: `scale(${1.6 - 0.6 * open})`, transformOrigin: 'left center'}}>
              OPEN</div>
          </div>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};

// The real email draft. The subject lands at once; the greeting and the lead-in fade in together; items 1 and 2
// (up to the first sentence of item 2) type out inside the first 7 seconds. Text is data.json's, cut not edited.
export const EmailCard: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const lines = data.email.split('\n');
  const item2 = lines[7].split('beside shopfront.')[0] + 'beside shopfront.';
  const typed1 = Math.min(lines[6].length, Math.max(0, Math.floor((frame - 10) * 4.0)));
  const typed2 = Math.min(item2.length, Math.max(0, Math.floor((frame - 58) * 4.2)));
  const head = interpolate(frame, [6, 14], [0, 1], clamp);
  const pop = spring({frame, fps, config: {damping: 15, stiffness: 180}});
  const rows: {text: string; bold?: boolean; fade?: number; typed?: boolean}[] = [
    {text: lines[0], bold: true},
    {text: ''},
    {text: lines[2], fade: head},
    {text: ''},
    {text: lines[4], fade: head},
    {text: ''},
    {text: lines[6].slice(0, typed1), typed: true},
    {text: ''},
    {text: item2.slice(0, typed2), typed: true},
  ];
  const typing = typed1 < lines[6].length ? 6 : typed2 < item2.length ? 8 : -1;
  return (
    <AbsoluteFill style={{background: '#f4f4f5', justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{width: 1600, background: PAPER, borderRadius: 28, padding: '54px 76px',
        boxShadow: '0 30px 90px rgba(0,0,0,.12)', transform: `translateY(${80 * (1 - pop)}px)`, opacity: pop}}>
        <div style={{fontSize: 34, lineHeight: 1.4, color: INK, whiteSpace: 'pre-wrap', fontWeight: 400}}>
          {rows.map((r, i) => (
            <div key={i} style={{fontWeight: r.bold ? 800 : 400, opacity: r.fade ?? 1,
              minHeight: r.text === '' ? 48 : undefined}}>
              {r.text}{i === typing && frame % 12 < 7 ? '▍' : ''}</div>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// White card, text only.
export const BuiltWith: React.FC = () => {
  const frame = useCurrentFrame();
  const a = interpolate(frame, [6, 20], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const b = interpolate(frame, [20, 34], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{background: PAPER, justifyContent: 'center', alignItems: 'center', fontFamily,
      flexDirection: 'column'}}>
      <div style={{fontSize: 64, fontWeight: 600, color: GREY, opacity: a, transform: `translateY(${20 * (1 - a)}px)`}}>
        Built this week with</div>
      <div style={{fontSize: 130, fontWeight: 800, color: INK, letterSpacing: -5, opacity: b,
        transform: `translateY(${24 * (1 - b)}px)`}}>SAM 3.1 and Muse Spark</div>
    </AbsoluteFill>
  );
};

// End card on light paper: the name gets the green detection box, then the two addresses.
export const EndCard: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = spring({frame: frame - 4, fps, config: {damping: 16, stiffness: 140}});
  const draw = interpolate(frame, [18, 30], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const lock = spring({frame: frame - 30, fps, config: {damping: 14, stiffness: 220}});
  const boxFade = interpolate(frame, [60, 80], [1, 0], clamp);
  const u1 = interpolate(frame, [40, 54], [0, 1], clamp);
  const u2 = interpolate(frame, [50, 64], [0, 1], clamp);
  const out = interpolate(frame, [400, 431], [1, 0], clamp);
  const pad = 30 - 12 * lock;
  return (
    <AbsoluteFill style={{background: '#fbfaf7', justifyContent: 'center', alignItems: 'center', fontFamily,
      flexDirection: 'column', opacity: out}}>
      <div style={{position: 'relative', fontSize: 150, fontWeight: 800, color: INK, letterSpacing: -6,
        opacity: t, transform: `scale(${0.94 + 0.06 * t})`}}>
        60-Second Fire Check
        <span style={{position: 'absolute', left: -pad, right: -pad, top: -pad + 14, bottom: -pad + 8,
          border: `5px solid ${GREEN}`, borderRadius: 8, clipPath: `inset(0 ${100 - draw * 100}% 0 0)`, opacity: boxFade}} />
      </div>
      <div style={{marginTop: 70, fontSize: 52, fontWeight: 600, color: INK, opacity: u1,
        transform: `translateY(${16 * (1 - u1)}px)`}}>firecheck.getnunchi.app</div>
      <div style={{marginTop: 20, fontSize: 38, fontWeight: 400, color: GREY, opacity: u2,
        transform: `translateY(${16 * (1 - u2)}px)`}}>github.com/tsetse012/60-second-fire-check</div>
    </AbsoluteFill>
  );
};
