import { useEffect, useMemo, useRef, useState } from "react";

/** Knight teaching motion: Walk / Run / Jump / Idle loops (no dead/hurt clips). */
const TEACH_CLIPS = [
  { prefix: "Walk", frames: 10, ms: 110 },
  { prefix: "Run", frames: 10, ms: 92 },
  { prefix: "Jump", frames: 10, ms: 85 },
  { prefix: "Idle", frames: 10, ms: 145 },
];

const IDLE_BREATHE = { prefix: "Idle", frames: 10, ms: 220 };

function frameSrc(prefix, frameNumber) {
  return `/freeknight/png/${prefix} (${frameNumber}).png`;
}

/**
 * Animated knight tutor. Lesson `minion_state` is intentionally ignored — always upbeat teaching motion:
 * Walk / Run / Jump / Idle in rotation, with a calm Idle stretch between bursts.
 */
export default function TutorMascot({ celebrate = false }) {
  const [prefix, setPrefix] = useState("Walk");
  const [frame, setFrame] = useState(1);
  const [imgError, setImgError] = useState(false);

  const timeoutsRef = useRef([]);

  useEffect(() => {
    setImgError(false);
    let cancelled = false;
    const clearMyTimeouts = () => {
      timeoutsRef.current.forEach((id) => window.clearTimeout(id));
      timeoutsRef.current = [];
    };

    function sleep(ms, fn) {
      const id = window.setTimeout(() => {
        timeoutsRef.current = timeoutsRef.current.filter((x) => x !== id);
        if (!cancelled) fn();
      }, ms);
      timeoutsRef.current.push(id);
    }

    /** Random pause between action clips (~0.85s–2s): slow Idle loops so it still feels alive. */
    function playBreathingThen(nextClipIndex) {
      let f = 1;
      const totalMs = 850 + Math.floor(Math.random() * 1150);

      function breatheStep() {
        if (cancelled) return;
        setPrefix(IDLE_BREATHE.prefix);
        setFrame(f);
        f = (f >= IDLE_BREATHE.frames ? 1 : f + 1);
      }

      breatheStep();
      let elapsed = 0;
      function tickBreath() {
        if (cancelled) return;
        sleep(IDLE_BREATHE.ms, () => {
          if (cancelled) return;
          elapsed += IDLE_BREATHE.ms;
          breatheStep();
          if (elapsed < totalMs) tickBreath();
          else playClip(nextClipIndex);
        });
      }
      tickBreath();
    }

    const activeClips = celebrate
      ? [
          { prefix: "Jump", frames: 10, ms: 82 },
          { prefix: "Run", frames: 10, ms: 88 },
          { prefix: "Attack", frames: 10, ms: 90 },
        ]
      : TEACH_CLIPS;

    function playClip(clipIndex) {
      if (cancelled) return;
      const c = activeClips[clipIndex % activeClips.length];
      let f = 1;

      function step() {
        if (cancelled) return;
        setPrefix(c.prefix);
        setFrame(f);
        if (f >= c.frames) {
          const ni = nextClipIndex(clipIndex, activeClips.length);
          playBreathingThen(ni);
          return;
        }
        f += 1;
        sleep(c.ms, step);
      }

      step();
    }

    const startIx = Math.floor(Math.random() * activeClips.length);
    playClip(startIx);

    return () => {
      cancelled = true;
      clearMyTimeouts();
    };
  }, [celebrate]);

  const src = useMemo(() => frameSrc(prefix, frame), [prefix, frame]);

  return (
    <div className="tutor-mascot tutor-mascot--teach" aria-hidden>
      {!imgError ? (
        <img
          className="tutor-mascot__sprite"
          src={src}
          alt=""
          onError={() => setImgError(true)}
          loading="eager"
          decoding="async"
        />
      ) : (
        <div className="tutor-mascot__fallback">Knight</div>
      )}
    </div>
  );
}

function nextClipIndex(current, n) {
  if (n <= 1) return 0;
  let j = Math.floor(Math.random() * (n - 1));
  if (j >= current) j += 1;
  return j;
}
