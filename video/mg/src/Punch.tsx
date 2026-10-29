import React from 'react';
import {AbsoluteFill, Freeze, interpolate, OffthreadVideo, staticFile, useCurrentFrame, Easing} from 'remotion';

// A real screen recording shown punched in: source point (cx, cy) is kept at the frame centre, scaled s0 -> s1
// over the shot. Edges are clamped so the frame never shows past the source. Used for every REAL shot.
export type PunchProps = {
  src: string; w: number; h: number;
  from: number;            // source seconds where the shot starts
  rate?: number;           // playback rate (speed ramp)
  cx: number; cy: number; s0: number; s1?: number;
  dur: number;             // shot length in frames (for the push)
  freeze?: boolean;        // hold the first frame, keep the push moving
  boxW?: number; boxH?: number; // target box (defaults to the full 1920x1080 frame)
  children?: React.ReactNode;    // overlays drawn in source pixel space (e.g. a blur over a field)
};

export const Punch: React.FC<PunchProps> = ({src, w, h, from, rate = 1, cx, cy, s0, s1 = s0, dur, freeze,
  boxW = 1920, boxH = 1080, children}) => {
  const frame = useCurrentFrame();
  const s = interpolate(frame, [0, dur], [s0, s1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.quad)});
  const vw = w * s, vh = h * s;
  const left = Math.min(0, Math.max(boxW - vw, boxW / 2 - cx * s));
  const top = Math.min(0, Math.max(boxH - vh, boxH / 2 - cy * s));
  const video = <OffthreadVideo src={staticFile(src)} startFrom={Math.round(from * 24)} playbackRate={rate} muted
    style={{width: vw, height: vh}} />;
  return (
    <AbsoluteFill style={{overflow: 'hidden', background: '#000'}}>
      <div style={{position: 'absolute', left, top, width: vw, height: vh}}>
        {freeze ? <Freeze frame={0}>{video}</Freeze> : video}
        <div style={{position: 'absolute', left: 0, top: 0, width: w, height: h, transform: `scale(${s})`,
          transformOrigin: '0 0'}}>{children}</div>
      </div>
    </AbsoluteFill>
  );
};
