import React from 'react';
import {Composition} from 'remotion';
import {Title} from './Title';
import {Beat} from './Beat';
import {Lines} from './Lines';
import {Count} from './Count';

export const Root: React.FC = () => (
  <>
    <Composition id="Title" component={Title} durationInFrames={120} fps={24} width={1920} height={1080} />
    <Composition id="BeatTrack" component={Beat} durationInFrames={48} fps={24} width={1920} height={1080}
      defaultProps={{word: 'TRACK', sub: 'SAM 3.1'}} />
    <Composition id="Opener" component={Lines} durationInFrames={156} fps={24} width={1920} height={1080}
      defaultProps={{lines: ['Every building', 'has a door', 'that should be closed.'], every: 52}} />
    <Composition id="Count" component={Count} durationInFrames={192} fps={24} width={1920} height={1080} />
  </>
);
