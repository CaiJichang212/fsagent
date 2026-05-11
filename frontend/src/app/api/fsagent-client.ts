import { ComposerSubmit } from "../components/task-composer";
import { FsAgentSession, ModelConfigResponse, PlanMeta, ReviewAction, TodoItem } from "../components/types";

export interface ReviewPlanPayload {
  action: "approve" | "edit" | "retry" | "cancel";
  todos?: TodoItem[];
  planMeta?: PlanMeta;
  feedback?: string;
  reason?: string;
}

export interface ReviewDecisionPayload {
  action: ReviewAction;
  reviewId?: string;
  editedSubject?: Record<string, unknown>;
  feedback?: string;
  reason?: string;
}

export async function getModelConfig(): Promise<ModelConfigResponse> {
  return request<ModelConfigResponse>("/api/model-config");
}

export async function createRun(input: ComposerSubmit): Promise<FsAgentSession> {
  return request<FsAgentSession>("/api/runs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function streamCreateRun(
  input: ComposerSubmit,
  onSession: (session: FsAgentSession) => void,
): Promise<FsAgentSession> {
  return streamRequest<FsAgentSession>(
    "/api/runs/stream",
    {
      method: "POST",
      body: JSON.stringify(input),
    },
    onSession,
  );
}

export async function getRun(sessionId: string): Promise<FsAgentSession> {
  return request<FsAgentSession>(`/api/runs/${sessionId}`);
}

export async function reviewPlan(sessionId: string, payload: ReviewPlanPayload): Promise<FsAgentSession> {
  return request<FsAgentSession>(`/api/runs/${sessionId}/review`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function streamReviewPlan(
  sessionId: string,
  payload: ReviewPlanPayload,
  onSession: (session: FsAgentSession) => void,
): Promise<FsAgentSession> {
  return streamRequest<FsAgentSession>(
    `/api/runs/${sessionId}/review/stream`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    onSession,
  );
}

export async function streamDecideReview(
  sessionId: string,
  reviewId: string,
  payload: ReviewDecisionPayload,
  onSession: (session: FsAgentSession) => void,
): Promise<FsAgentSession> {
  return streamRequest<FsAgentSession>(
    `/api/runs/${sessionId}/reviews/${reviewId}/decision/stream`,
    {
      method: "POST",
      body: JSON.stringify({ reviewId, ...payload }),
    },
    onSession,
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {}
  return `API request failed with ${response.status}`;
}

async function streamRequest<T>(
  path: string,
  init: RequestInit,
  onMessage: (message: T) => void,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  if (!response.body) {
    throw new Error("API stream did not return a body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let last: T | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const message = parseSessionFrame<T>(frame);
      if (message) {
        last = message;
        onMessage(message);
      }
    }
  }

  buffer += decoder.decode();
  const finalMessage = parseSessionFrame<T>(buffer);
  if (finalMessage) {
    last = finalMessage;
    onMessage(finalMessage);
  }
  if (!last) {
    throw new Error("API stream ended without a session event");
  }
  return last;
}

function parseSessionFrame<T>(frame: string): T | null {
  const lines = frame.split(/\r?\n/);
  const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
  if (event && event !== "session") return null;
  const data = lines
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("\n");
  return data ? (JSON.parse(data) as T) : null;
}
