import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, Easing} from 'remotion';
import {fontFamily} from './theme';

// Act 1 opener: one phrase per beat, white on black, each phrase rises in and the last one stays.
export const Lines: React.FC<{lines: string[]; every: number; dark?: boolean}> = ({lines, every, dark = true}) => {
  const frame = useCurrentFrame();
  const idx = Math.min(lines.length - 1, Math.floor(frame / every));
  const local = frame - idx * every;
  const rise = interpolate(local, [0, 8], [1, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const blur = interpolate(local, [0, 6], [12, 0], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{background: dark ? '#000' : '#fff', justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{fontSize: 120, fontWeight: 800, letterSpacing: -4, color: dark ? '#fff' : '#0b0b0c',
        transform: `translateY(${40 * rise}px)`, filter: `blur(${blur}px)`, opacity: 1 - rise}}>
        {lines[idx]}
      </div>
    </AbsoluteFill>
  );
};
