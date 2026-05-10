import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurriculum, getHealth, getProgress, postLesson, postProgress } from "./api/client.js";
import LessonStage from "./components/LessonStage.jsx";

export default function App() {
  const [userId, setUserId] = useState("demo-1");
  const [lessonId, setLessonId] = useState("");
  const [profile, setProfile] = useState("weak");
  const [event, setEvent] = useState("lesson_start");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [backendOnline, setBackendOnline] = useState(true);
  const [result, setResult] = useState(null);
  const [curriculum, setCurriculum] = useState(null);
  const [view, setView] = useState("setup"); // setup | lesson
  const [resumeStep, setResumeStep] = useState(0);
  /** Building next lesson after wrap-up (stay in lesson view). */
  const [loadingNextLesson, setLoadingNextLesson] = useState(false);
  const lastSavedRef = useRef({ lessonId: "", step: -1 });

  const lessonTitle = useMemo(() => {
    if (!lessonId || !curriculum?.lessons) return "";
    const row = curriculum.lessons.find((l) => l.lesson_id === lessonId);
    return row?.title ?? "";
  }, [curriculum, lessonId]);

  const lessonIndex = useMemo(() => {
    if (!lessonId || !curriculum?.lessons) return -1;
    return curriculum.lessons.findIndex((l) => l.lesson_id === lessonId);
  }, [curriculum, lessonId]);
  const isFinalLesson = useMemo(() => {
    if (!curriculum?.lessons?.length || lessonIndex < 0) return false;
    return lessonIndex >= curriculum.lessons.length - 1;
  }, [curriculum, lessonIndex]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getCurriculum(), getProgress(userId)])
      .then(([c, p]) => {
        if (cancelled) return;
        setBackendOnline(true);
        setCurriculum(c);
        const restoredLesson = p?.current_lesson_id;
        if (restoredLesson) setLessonId(restoredLesson);
        else if (!lessonId && c?.lessons?.length) setLessonId(c.lessons[0].lesson_id);
        /* Always start lessons from step 0 — no resume-from-middle after exit or profile change. */
        setResumeStep(0);
      })
      .catch((err) => {
        if (cancelled) return;
        setBackendOnline(false);
        setError(err?.message || String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  function resetSessionStepForLesson(id) {
    setResumeStep(0);
    lastSavedRef.current = { lessonId: "", step: -1 };
    if (id) {
      void postProgress({
        user_id: userId,
        action: "save_state",
        lesson_id: id,
        step_index: 0,
      });
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    /* Fresh run always from first step */
    setResumeStep(0);
    lastSavedRef.current = { lessonId: "", step: -1 };
    try {
      await getHealth();
      setBackendOnline(true);
      const body = {
        user_id: userId,
        profile,
        event,
      };
      if (event === "game_failed_return") {
        // Remediation should always request extra support tone.
        body.profile = "weak";
      }
      if (lessonId) {
        body.lesson_id = lessonId;
      }
      if (import.meta.env.DEV) {
        console.debug("[App] POST /lesson →", body);
      }
      const data = await postLesson(body);
      if (import.meta.env.DEV) {
        console.debug("[App] lesson OK", {
          lesson_id: data?.lesson_id,
          textChars: data?.lesson_text?.length ?? 0,
          chunks: data?.source_chunk_ids?.length,
        });
      }
      setResumeStep(0);
      setResult(data);
      setView("lesson");
    } catch (err) {
      const msg = err?.message || String(err);
      if (msg.includes("Failed to fetch") || msg.includes("ECONNREFUSED")) {
        setBackendOnline(false);
        setError("Backend is offline. Start backend server in backend folder with .\\run.ps1 and try again.");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }

  function exitLesson() {
    if (lessonId) resetSessionStepForLesson(lessonId);
    else setResumeStep(0);
    setView("setup");
    setResult(null);
    setEvent("lesson_start");
  }

  async function onLessonDone() {
    /* Wrap-up: optional continuous flow into next lesson at same learner level */
    if (event === "wrap_up_success" && lessonId && curriculum?.lessons?.length) {
      try {
        await postProgress({
          user_id: userId,
          action: "record_quiz",
          lesson_id: lessonId,
          score: 1.0,
        });
      } catch {
        /* still try to advance UI */
      }

      const lessons = curriculum.lessons;
      const idx = lessons.findIndex((l) => l.lesson_id === lessonId);
      const next = idx >= 0 ? lessons[idx + 1] : null;

      if (next?.lesson_id && !isFinalLesson) {
        setLoadingNextLesson(true);
        setError("");
        try {
          await postProgress({
            user_id: userId,
            action: "set_current",
            lesson_id: next.lesson_id,
          });
          await postProgress({
            user_id: userId,
            action: "save_state",
            lesson_id: next.lesson_id,
            step_index: 0,
          });
        } catch {
          /* continue */
        }

        try {
          await getHealth();
          const body = {
            user_id: userId,
            profile,
            event: "lesson_start",
            lesson_id: next.lesson_id,
          };
          const data = await postLesson(body);
          /* Same batch so title + content stay aligned */
          setLessonId(next.lesson_id);
          setResumeStep(0);
          lastSavedRef.current = { lessonId: next.lesson_id, step: 0 };
          setResult(data);
          setEvent("lesson_start");
        } catch (err) {
          const msg = err?.message || String(err);
          if (msg.includes("Failed to fetch") || msg.includes("ECONNREFUSED")) {
            setBackendOnline(false);
            setError("Backend is offline. Try again after starting the server.");
          } else {
            setError(msg);
          }
          setView("setup");
          setResult(null);
        } finally {
          setLoadingNextLesson(false);
        }
        return;
      }

      /* Final chapter wrap-up → home */
      setView("setup");
      setResumeStep(0);
      setResult(null);
      setEvent("lesson_start");
      lastSavedRef.current = { lessonId: "", step: -1 };
      return;
    }

    setView("setup");
    setResumeStep(0);
    setResult(null);
    setEvent("lesson_start");
    lastSavedRef.current = { lessonId: "", step: -1 };
  }

  const onStepChange = useCallback((stepIndex) => {
    if (!lessonId) return;
    if (
      lastSavedRef.current.lessonId === lessonId &&
      lastSavedRef.current.step === stepIndex
    ) {
      return;
    }
    lastSavedRef.current = { lessonId, step: stepIndex };
    setResumeStep(stepIndex);
    void postProgress({
      user_id: userId,
      action: "save_state",
      lesson_id: lessonId,
      step_index: stepIndex,
    });
  }, [lessonId, userId]);

  if (view === "lesson" && result?.lesson_text) {
    const stageKey = [
      result.lesson_id || lessonId,
      event,
      (result.lesson_text || "").length,
    ].join(":");
    return (
      <LessonStage
        key={stageKey}
        lessonText={result.lesson_text}
        lessonId={result.lesson_id || lessonId}
        lessonTitle={lessonTitle}
        event={event}
        isFinalLesson={isFinalLesson}
        initialStep={resumeStep}
        loadingNextLesson={loadingNextLesson}
        onStepChange={onStepChange}
        onClose={exitLesson}
        onLessonDone={onLessonDone}
      />
    );
  }

  return (
    <main>
      <h1>Grade 6 science</h1>

      <form className="card" onSubmit={onSubmit}>
        <h2 className="card__h">Start lesson</h2>
        <label htmlFor="userId">User id</label>
        <input id="userId" value={userId} onChange={(e) => setUserId(e.target.value)} />

        <label htmlFor="lessonId">Topic</label>
        <select id="lessonId" value={lessonId} onChange={(e) => setLessonId(e.target.value)}>
          <option value="">Choose…</option>
          {(curriculum?.lessons || []).map((l) => (
            <option key={l.lesson_id} value={l.lesson_id}>
              {l.title}
            </option>
          ))}
        </select>

        <label htmlFor="profile">Learner level</label>
        <select
          id="profile"
          value={profile}
          onChange={(e) => {
            const next = e.target.value;
            setProfile(next);
            resetSessionStepForLesson(lessonId || undefined);
          }}
        >
          <option value="weak">weak</option>
          <option value="average">average</option>
          <option value="strong">smart</option>
        </select>

        <label htmlFor="event">Mood / event</label>
        <select id="event" value={event} onChange={(e) => setEvent(e.target.value)}>
          <option value="lesson_start">Lesson start</option>
          <option value="game_failed_return">Student failed in game - extra support</option>
          <option value="wrap_up_success">Student passed game - wrap up + advance</option>
        </select>

        <button type="submit" disabled={loading || !lessonId || !backendOnline}>
          {loading ? "Building lesson…" : "Start lesson"}
        </button>
        {!backendOnline ? <p className="err">Backend disconnected on port 8000. Start backend and retry.</p> : null}
        {error ? <p className="err">{error}</p> : null}
      </form>
    </main>
  );
}
