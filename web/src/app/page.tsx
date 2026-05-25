"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Navbar,
  NavbarBrand,
  NavbarContent,
  NavbarItem,
  Link,
  Button,
  Tabs,
  Tab,
} from "@heroui/react";
import IntroPanel from "@/components/IntroPanel";
import LoopPanel from "@/components/LoopPanel";
import InspectorPanel from "@/components/InspectorPanel";

const ARXIV = "https://arxiv.org/abs/2604.11811";
const GITHUB = "https://github.com/wbopan/mstar";
const ease: [number, number, number, number] = [0.22, 1, 0.36, 1];

type Viz = {
  Figure1: {
    init: () => Promise<void>;
    beginGrowth: () => void;
    growNext: () => boolean;
  };
  Figure2: {
    init: () => void;
    reset: () => void;
    play: () => void;
    stop: () => void;
    togglePlay: () => void;
    step: () => void;
    setOnRound: (fn: () => void) => void;
  };
  Inspector: { init: () => void; onResize: () => void };
};

export default function Home() {
  const [selected, setSelected] = useState<string>("intro");
  const [playing, setPlaying] = useState(false);

  const vizRef = useRef<Viz | null>(null);
  const readyRef = useRef(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const startLoop = () => {
    const viz = vizRef.current;
    if (!viz || !readyRef.current) return;
    viz.Figure1.beginGrowth();
    viz.Figure2.reset();
    viz.Figure2.play();
    setPlaying(true);
  };

  // Mount the d3 modules once (client only). Mirrors the old main.js wiring.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const viz = (await import("@/viz")) as unknown as Viz;
      if (cancelled) return;
      vizRef.current = viz;
      viz.Figure2.init();
      viz.Inspector.init();
      await viz.Figure1.init();
      if (cancelled) return;
      viz.Figure2.setOnRound(() => viz.Figure1.growNext());
      readyRef.current = true;
      if (selectedRef.current === "loop") startLoop();
    })().catch((err) => console.error("viz init", err));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React to tab changes, like main.js onShow.
  useEffect(() => {
    const viz = vizRef.current;
    if (!viz || !readyRef.current) return;
    if (selected === "loop") {
      startLoop();
    } else {
      viz.Figure2.stop();
      setPlaying(false);
    }
    if (selected === "inspect") viz.Inspector.onResize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // Keep the trend chart crisp on window resize.
  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const onResize = () => {
      clearTimeout(t);
      t = setTimeout(() => vizRef.current?.Inspector.onResize(), 180);
    };
    window.addEventListener("resize", onResize);
    return () => {
      clearTimeout(t);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const onPlay = () => {
    vizRef.current?.Figure2.togglePlay();
    setPlaying((p) => !p);
  };
  const onStep = () => {
    vizRef.current?.Figure2.step();
    setPlaying(false);
  };

  return (
    <>
      <Navbar maxWidth="xl" isBordered>
        <NavbarBrand className="gap-2">
          <span className="text-[20px] font-semibold text-foreground">
            M<span className="text-primary">★</span>
          </span>
          <span className="hidden text-[13px] text-default-500 sm:inline">
            Every Task Deserves Its Own Memory Harness
          </span>
        </NavbarBrand>
        <NavbarContent justify="end">
          <NavbarItem>
            <Link href={ARXIV} isExternal size="sm" color="foreground">
              arXiv
            </Link>
          </NavbarItem>
          <NavbarItem>
            <Link href={GITHUB} isExternal size="sm" color="foreground">
              GitHub
            </Link>
          </NavbarItem>
        </NavbarContent>
      </Navbar>

      <main className="mx-auto w-[92%] max-w-[1120px] pb-16 pt-7">
        {/* Title block */}
        <motion.header
          className="pb-4 pt-2"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease }}
        >
          <h1 className="text-[clamp(28px,4.2vw,44px)] font-bold leading-[1.1] tracking-tight text-foreground">
            Every Task Deserves Its Own Memory Harness
          </h1>
          <p className="mt-3 max-w-[760px] text-[15px] text-default-700">
            Wenbo Pan, Shujie Liu, Xiangyang Zhou, Shiwei Zhang, Wanlu Shi,
            Mirror Xu, Xiaohua Jia
            <span className="text-default-500">
              {" "}
              — City University of Hong Kong · Microsoft
            </span>
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Button
              as="a"
              href={ARXIV}
              target="_blank"
              rel="noopener"
              color="primary"
            >
              Read the paper
            </Button>
            <Button
              as="a"
              href={GITHUB}
              target="_blank"
              rel="noopener"
              variant="bordered"
            >
              Source code
            </Button>
          </div>
        </motion.header>

        <Tabs
          aria-label="M★ sections"
          selectedKey={selected}
          onSelectionChange={(k) => setSelected(String(k))}
          variant="underlined"
          color="primary"
          size="lg"
          destroyInactiveTabPanel={false}
          classNames={{ base: "mt-4", panel: "pt-6" }}
        >
          <Tab key="intro" title="Introduction">
            <IntroPanel />
          </Tab>
          <Tab key="loop" title="Evolution Loop">
            <LoopPanel playing={playing} onPlay={onPlay} onStep={onStep} />
          </Tab>
          <Tab key="inspect" title="Inspector">
            <InspectorPanel />
          </Tab>
        </Tabs>
      </main>
    </>
  );
}
