import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {fontFamily, INK, PAPER} from './theme';

// One-word chapter beat (TRACK / NAME / INSPECT): slams in on the music hit, model name follows.
export const Beat: React.FC<{word: string; sub: string}> = ({word, sub}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const hit = spring({frame, fps, config: {damping: 11, stiffness: 260, mass: 0.6}});
  const subIn = interpolate(frame, [8, 16], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{background: PAPER, justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{fontSize: 330, fontWeight: 800, color: INK, letterSpacing: -14,
        transform: `scale(${2.2 - 1.2 * hit})`, opacity: Math.min(1, hit * 1.5)}}>{word}</div>
      <div style={{marginTop: -10, fontSize: 46, fontWeight: 600, color: '#6b6b70', letterSpacing: 2,
        opacity: subIn, transform: `translateY(${16 * (1 - subIn)}px)`}}>{sub}</div>
    </AbsoluteFill>
  );
};
