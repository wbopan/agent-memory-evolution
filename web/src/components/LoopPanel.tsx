"use client";

import { Button } from "@heroui/react";

type Props = {
  playing: boolean;
  onPlay: () => void;
  onStep: () => void;
};

export default function LoopPanel({ playing, onPlay, onStep }: Props) {
  return (
    <div>
      <div className="merge16x9 border border-default-200 shadow-medium">
        <div className="merge-loop">
          <svg id="loop-svg" role="img" aria-label="Animated evolution loop" />
        </div>
        <div className="merge-phylo">
          <svg
            id="phylo-svg"
            role="img"
            aria-label="Radial phylogenetic tree growing as the loop runs"
          />
          <div className="phylo-tooltip" id="phylo-tip" hidden />
        </div>
      </div>

      <div className="merge-bar">
        <div className="flex flex-wrap items-center gap-3">
          <Button color="primary" size="sm" onPress={onPlay}>
            {playing ? "Pause" : "Play"}
          </Button>
          <Button variant="bordered" size="sm" onPress={onStep}>
            Step
          </Button>
          <span className="mb-narr">
            <b id="ln-title" className="ln-title" />{" "}
            <span id="ln-body" className="ln-body" />
          </span>
          <span id="ln-step" className="ln-step" hidden />
        </div>
        <div className="phylo-legend" id="phylo-legend" aria-label="Task colour key" />
      </div>

      <ol className="loop-steps" id="loop-steps" hidden />
      <div className="phylo-readout" id="phylo-readout" hidden />

      <p className="figcap">
        <b>Figure.</b> Left: one turn of the reflective code-evolution loop —
        sample a parent, evaluate it, mutate it from its failures, and pass
        quality gates. Right: the phylogeny of evolved programs. Each completed
        turn of the loop grows one new program on each of the four task
        quadrants, in the order they were produced during the recorded runs.
      </p>
    </div>
  );
}
