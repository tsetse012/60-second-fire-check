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

// End of TRACK: the 144.9 s walk splits into 10-second windows, then stitches back into one green bar.
export const Windows: React.FC = () => {
  const frame = useCurrentFrame();
  const total = 144.9, W = 1600;
  const n = Math.ceil(total / 10);
  const draw = interpolate(frame, [0, 12], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const split = interpolate(frame, [14, 34], [0, 1], {...clamp, easing: Easing.out(Easing.back(1.4))})
    - interpolate(frame, [58, 76], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const green = interpolate(frame, [70, 80], [0, 1], clamp);
  const gap = 18 * split;
  const usable = W - gap * (n - 1);
  let x = 0;
  const segs = Array.from({length: n}, (_, i) => {
    const len = Math.min(10, total - i * 10);
    const sw = (usable * len) / total;
    const seg = {x, sw, i};
    x += sw + gap;
    return seg;
  });
  const lift = (i: number) => 26 * split * (i % 2 ? 1 : -1);
  return (
    <AbsoluteFill style={{background: PAPER, fontFamily, justifyContent: 'center', alignItems: 'center'}}>
      <div style={{position: 'relative', width: W, height: 40, clipPath: `inset(-200px ${100 - draw * 100}% -200px 0)`}}>
        {segs.map(({x, sw, i}) => (
          <div key={i} style={{position: 'absolute', left: x, top: lift(i), width: sw, height: 40, borderRadius: 6,
            background: green > 0 ? `color-mix(in srgb, ${GREEN} ${green * 100}%, ${INK})` : INK,
            outline: split > 0.05 ? `3px solid ${GREEN}` : 'none', outlineOffset: 4}} />
        ))}
        <div style={{position: 'absolute', left: 0, top: 80, fontSize: 34, fontWeight: 600, color: GREY}}>0 s</div>
        <div style={{position: 'absolute', right: 0, top: 80, fontSize: 34, fontWeight: 600, color: GREY}}>144.9 s</div>
        <div style={{position: 'absolute', left: segs[0].x, top: -110, fontSize: 40, fontWeight: 800, color: GREEN,
          opacity: Math.max(0, split)}}>10 s</div>
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
export const Inspect: React.FC<{chipAt: (i: number) => number}> = ({chipAt}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const chipsOut = interpolate(frame, [168, 184], [0, 1], clamp);
  const lanesIn = interpolate(frame, [186, 204], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const merge = interpolate(frame, [340, 372], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const listOut = interpolate(frame, [440, 456], [0, 1], clamp);
  const doorIn = spring({frame: frame - 452, fps, config: {damping: 15, stiffness: 160}});
  const open = spring({frame: frame - 532, fps, config: {damping: 9, stiffness: 260}});
  const asks = [482, 506];
  return (
    <AbsoluteFill style={{background: PAPER, fontFamily}}>
      {/* A — tool calls */}
      {chipsOut < 1 && (
        <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', opacity: 1 - chipsOut,
          transform: `translateY(${-80 * chipsOut}px)`}}>
          <div style={{width: 1640, display: 'flex', flexWrap: 'wrap', gap: 22, justifyContent: 'center'}}>
            {data.tools.map((t, i) => {
              const s = spring({frame: frame - chipAt(i), fps, config: {damping: 13, stiffness: 240, mass: 0.6}});
              return (
                <span key={i} style={{fontFamily: MONO, fontSize: 32, color: INK, background: '#f2f2f4',
                  border: `2px solid ${t.startsWith('submit') ? GREEN : '#dcdce0'}`, padding: '12px 20px', borderRadius: 12,
                  opacity: frame < chipAt(i) ? 0 : Math.min(1, s * 1.5),
                  transform: `translateX(${600 * (1 - s)}px) scale(${0.7 + 0.3 * s})`}}>{t}</span>
              );
            })}
          </div>
        </AbsoluteFill>
      )}
      {/* B/C — two lanes, then one checklist */}
      {lanesIn > 0 && listOut < 1 && (
        <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', opacity: lanesIn * (1 - listOut),
          transform: 'scale(1.3)'}}>
          <div style={{position: 'absolute', transform: `translateX(${-330 * (1 - merge)}px)`}}>
            <Lane title={merge > 0.5 ? 'check 1 + check 2' : 'check 1'} start={200} width={600} />
          </div>
          <div style={{position: 'absolute', transform: `translateX(${330 * (1 - merge)}px)`, opacity: 1 - merge}}>
            <Lane title="check 2" start={212} width={600} />
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

// The real email draft, first lines typing onto a card.
export const EmailCard: React.FC<{cps: number}> = ({cps}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const text = data.email.split('\n').slice(0, 5).join('\n');
  const n = Math.max(0, Math.floor((frame - 10) * cps));
  const shown = text.slice(0, n);
  const pop = spring({frame, fps, config: {damping: 15, stiffness: 180}});
  return (
    <AbsoluteFill style={{background: '#f4f4f5', justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{width: 1400, minHeight: 600, background: PAPER, borderRadius: 28, padding: '56px 72px',
        boxShadow: '0 30px 90px rgba(0,0,0,.12)', transform: `translateY(${80 * (1 - pop)}px)`, opacity: pop}}>
        <div style={{fontSize: 40, lineHeight: 1.45, color: INK, whiteSpace: 'pre-wrap', fontWeight: 400}}>
          {shown.split('\n').map((l, i) => (
            <div key={i} style={{fontWeight: i === 0 ? 800 : 400, minHeight: i === 1 || i === 3 ? 22 : undefined}}>
              {l}{i === shown.split('\n').length - 1 && n < text.length && frame % 12 < 7 ? '▍' : ''}</div>
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
