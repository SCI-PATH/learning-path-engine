import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import LessonStepContent from "./LessonStepContent.jsx";
import TutorMascot from "./TutorMascot.jsx";
import { getLessonVisual } from "../lessonVisuals.js";
import { splitLessonIntoSteps } from "../utils/splitLessonSteps.js";

export default function LessonStage({
  lessonText,
  lessonId,
  lessonTitle,
  event,
  isFinalLesson = false,
  initialStep = 0,
  loadingNextLesson = false,
  onStepChange,
  onClose,
  onLessonDone,
}) {
  const steps = useMemo(() => splitLessonIntoSteps(lessonText), [lessonText]);
  const visual = useMemo(() => getLessonVisual(lessonId), [lessonId]);
  const [stepIndex, setStepIndex] = useState(() => Math.max(0, Number(initialStep) || 0));
  const isWrapUp = event === "wrap_up_success";
  /** Match saved resume before paint — avoids flashing step 0 then jumping. */
  useLayoutEffect(() => {
    const s = Math.max(0, Number(initialStep) || 0);
    setStepIndex(s);
  }, [lessonText, lessonId, initialStep]);

  /**
   * One-time persistent sync when this lesson bundle opens (never step 0 by mistake).
   * Step changes afterward only fire from Continue / Back — avoids DB thrash & render loops.
   */
  useEffect(() => {
    if (isWrapUp) return;
    const s = Math.max(0, Number(initialStep) || 0);
    if (import.meta.env.DEV) {
      console.debug("[LessonStage] hydrate progress", { lessonId, step: s });
    }
    onStepChange?.(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: lesson identity only + wrap-up gate
  }, [lessonText, lessonId, isWrapUp]);

  const isLast = steps.length === 0 || stepIndex >= steps.length - 1;
  const current = stripTopicPrefix(steps[stepIndex] ?? "", lessonTitle);
  const progress = steps.length ? (stepIndex + 1) / steps.length : 1;
  const stepWordCount = current.trim()
    ? current.trim().split(/\s+/).filter(Boolean).length
    : 0;
  const wrapUpText = isFinalLesson
    ? "Congratulations! You have successfully completed Grade 6 Science. Great work, and good luck in your next grade!"
    : `Congratulations! You completed ${lessonTitle}. You are ready for the next chapter.`;

  function onPrimary() {
    if (isWrapUp) {
      onLessonDone?.();
      return;
    }
    if (!isLast) {
      const next = Math.min(stepIndex + 1, Math.max(steps.length - 1, 0));
      setStepIndex(next);
      if (import.meta.env.DEV) {
        console.debug("[LessonStage] continue", { lessonId, from: stepIndex, to: next });
      }
      onStepChange?.(next);
      return;
    }
    onLessonDone?.();
  }

  function onBack() {
    const prev = Math.max(stepIndex - 1, 0);
    setStepIndex(prev);
    if (import.meta.env.DEV) {
      console.debug("[LessonStage] back", { lessonId, from: stepIndex, to: prev });
    }
    onStepChange?.(prev);
  }

  return (
    <div className="lesson-stage">
      {loadingNextLesson ? (
        <div className="lesson-stage__loading-overlay" role="status" aria-live="polite">
          <p className="lesson-stage__loading-text">Loading next lesson…</p>
        </div>
      ) : null}
      <header className="lesson-stage__bar">
        <button
          type="button"
          className="lesson-stage__iconbtn"
          onClick={onClose}
          aria-label="Exit lesson"
          disabled={loadingNextLesson}
        >
          ✕
        </button>
        <div className="lesson-stage__titlewrap">
          <p className="lesson-stage__label">Lesson</p>
          <h2 className="lesson-stage__title">
            {isWrapUp && isFinalLesson ? "Grade 6 Complete" : lessonTitle || lessonId || "Science"}
          </h2>
        </div>
        <span className="lesson-stage__pill">
          {isWrapUp ? "Done" : `${stepIndex + 1} / ${Math.max(steps.length, 1)}`}
        </span>
      </header>

      <div className="lesson-stage__scene" style={{ background: visual.gradient }}>
        <div className="lesson-stage__scene-float" key={stepIndex}>
          <span className="lesson-stage__emoji" role="img" aria-label="">
            {visual.emoji}
          </span>
          <p className="lesson-stage__caption">{lessonTitle || visual.caption}</p>
        </div>
      </div>

      <div className="lesson-stage__dialog">
        <div className="lesson-stage__mascot-wrap">
          <TutorMascot celebrate={isWrapUp} />
        </div>
        <div className="speech-bubble" role="region" aria-live="polite">
          <p className="speech-bubble__eyebrow">King Arthur says</p>
          <div className="speech-bubble__body">
            {isWrapUp ? (
              <LessonStepContent text={wrapUpText} />
            ) : (
              <LessonStepContent text={current} />
            )}
          </div>
          {stepWordCount > 0 && !isWrapUp ? (
            <p className="speech-bubble__hint">Read at your own pace — tap Continue when it feels clear.</p>
          ) : null}
        </div>
      </div>

      <footer className="lesson-stage__footer">
        {!isWrapUp ? (
          <>
            <div className="lesson-stage__dots" aria-hidden>
              {steps.map((_, i) => (
                <span key={i} className={`lesson-stage__dot ${i <= stepIndex ? "lesson-stage__dot--on" : ""}`} />
              ))}
            </div>
            <div
              className="lesson-stage__progress"
              role="progressbar"
              aria-valuenow={Math.round(progress * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="lesson-stage__progress-fill" style={{ width: `${progress * 100}%` }} />
            </div>
          </>
        ) : null}
        <div className="lesson-stage__actions">
          {!isWrapUp ? (
            <button
              type="button"
              className="lesson-stage__back"
              onClick={onBack}
              disabled={stepIndex <= 0 || loadingNextLesson}
            >
              Back
            </button>
          ) : null}
          <button
            type="button"
            className="lesson-stage__cta"
            onClick={onPrimary}
            disabled={loadingNextLesson}
          >
            {isWrapUp ? (isFinalLesson ? "Go to Home" : "Next chapter") : isLast ? "Done" : "Continue"}
          </button>
        </div>
      </footer>
    </div>
  );
}

function stripTopicPrefix(text, lessonTitle) {
  const t = (text || "").trim();
  if (!t) return t;
  if (!lessonTitle) return t;
  const esc = lessonTitle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`^${esc}\\s*[:\\-–—]?\\s*`, "i");
  return t.replace(re, "").trim();
}
