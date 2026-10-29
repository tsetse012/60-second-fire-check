import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {fontFamily, INK, PAPER, GREEN} from './theme';

// The title is "detected": a tracking box draws around each word, locks, then fades,
// the same way SAM boxes lock onto objects in the product overlay.
const WORDS = ['60-Second', 'Fire', 'Check'];

const Box: React.FC<{start: number; children: React.ReactNode}> = ({start, children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const draw = interpolate(frame - start, [0, 8], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic)});
  const lock = spring({frame: frame - start - 8, fps, config: {damping: 14, stiffness: 220}});
  const fade = interpolate(frame - start, [26, 40], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const textIn = interpolate(frame - start, [2, 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const pad = 18 - 8 * lock;
  return (
    <span style={{position: 'relative', display: 'inline-block', margin: '0 22px', opacity: textIn}}>
      {children}
      <span style={{position: 'absolute', left: -pad, right: -pad, top: -pad + 10, bottom: -pad + 6,
        border: `4px solid ${GREEN}`, clipPath: `inset(0 ${100 - draw * 100}% 0 0)`, opacity: fade, borderRadius: 6}} />
      <span style={{position: 'absolute', left: -pad, top: -pad - 26, fontSize: 22, fontWeight: 600,
        color: PAPER, background: GREEN, padding: '2px 8px', borderRadius: 4, opacity: fade * draw,
        letterSpacing: 1}}>{`id ${WORDS.indexOf(String(children)) + 1}`}</span>
    </span>
  );
};

export const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const settle = interpolate(frame, [70, 100], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic)});
  return (
    <AbsoluteFill style={{background: PAPER, justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{fontSize: 150, fontWeight: 800, color: INK, letterSpacing: -5,
        transform: `scale(${1 - 0.04 * settle})`}}>
        {WORDS.map((w, i) => <Box key={w} start={6 + i * 12}>{w}</Box>)}
      </div>
      <div style={{position: 'absolute', bottom: 210, fontSize: 40, fontWeight: 400, color: '#6b6b70',
        opacity: settle, transform: `translateY(${20 * (1 - settle)}px)`}}>
        Walk a corridor. Get the inspection.
      </div>
    </AbsoluteFill>
  );
};
