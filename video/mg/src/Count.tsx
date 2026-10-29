import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {fontFamily, INK, GREEN} from './theme';

// Act 4 number beat: "18 items, counted by hand." then the count runs up to 14 found.
export const Count: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = interpolate(frame, [0, 10], [0, 1], {extrapolateRight: 'clamp'});
  const n = Math.round(interpolate(frame, [40, 100], [0, 14], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic)}));
  const pop = spring({frame: frame - 100, fps, config: {damping: 10, stiffness: 240}});
  return (
    <AbsoluteFill style={{background: '#fff', justifyContent: 'center', alignItems: 'center', fontFamily}}>
      <div style={{position: 'absolute', top: 250, fontSize: 52, fontWeight: 600, color: '#6b6b70', opacity: a}}>
        18 items, counted by hand.
      </div>
      <div style={{display: 'flex', alignItems: 'baseline', gap: 28, opacity: interpolate(frame, [36, 44], [0, 1],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}}>
        <span style={{fontSize: 360, fontWeight: 800, color: INK, letterSpacing: -16,
          transform: `scale(${1 + 0.06 * pop})`, display: 'inline-block'}}>{n}</span>
        <span style={{fontSize: 96, fontWeight: 800, color: GREEN}}>found.</span>
      </div>
    </AbsoluteFill>
  );
};
