import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { Channel, Message } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, Input, Label, TextArea, errorMessage } from "../components/ui";
import { useWorkspace } from "../project/workspaceContext";
import { useAuth } from "../auth/AuthContext";

// A capture group makes split() return mentions at odd indexes, so no stateful /g regex is needed.
const MENTION_SPLIT = /(@[a-z0-9][a-z0-9_-]{0,39})/i;

function Body({ text }: { text: string }) {
  if (!text) return <em className="text-stone-400">message deleted</em>;
  return (
    <>
      {text.split(MENTION_SPLIT).map((part, index) =>
        index % 2 === 1 ? (
          <span key={index} className="rounded bg-amber-100 px-0.5 font-medium text-amber-900">
            {part}
          </span>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

export function DiscussionsView() {
  const { project, subscribe, canWrite, isModerator } = useWorkspace();
  const { user } = useAuth();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelId, setChannelId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);
  const [newChannel, setNewChannel] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadChannels = useCallback(async () => {
    const list = await api<Channel[]>(`/projects/${project.id}/channels`);
    setChannels(list);
    setChannelId((current) => current ?? list.find((c) => c.name === "general")?.id ?? list[0]?.id ?? null);
  }, [project.id]);

  const loadMessages = useCallback(async () => {
    if (!channelId) return;
    setMessages(await api<Message[]>(`/projects/${project.id}/channels/${channelId}/messages?limit=200`));
  }, [project.id, channelId]);

  useEffect(() => {
    loadChannels().catch((err: unknown) => setError(errorMessage(err)));
  }, [loadChannels]);

  useEffect(() => {
    loadMessages().catch((err: unknown) => setError(errorMessage(err)));
  }, [loadMessages]);

  useEffect(() => {
    return subscribe((event: RealtimeEnvelope) => {
      if (event.event_type === "conversation.message.created" || event.event_type === "conversation.message.updated") {
        void loadMessages();
      }
      if (event.event_type === "conversation.channel.created") {
        void loadChannels();
      }
    });
  }, [subscribe, loadMessages, loadChannels]);

  const threads = useMemo(() => {
    const roots = messages.filter((m) => !m.parent_message_id);
    const replies = new Map<string, Message[]>();
    for (const m of messages) {
      if (m.parent_message_id) {
        replies.set(m.parent_message_id, [...(replies.get(m.parent_message_id) ?? []), m]);
      }
    }
    return { roots, replies };
  }, [messages]);

  const activeChannel = channels.find((c) => c.id === channelId);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!channelId || !draft.trim()) return;
    setError(null);
    try {
      await api<Message>(`/projects/${project.id}/channels/${channelId}/messages`, {
        method: "POST",
        json: { body: draft.trim(), parent_message_id: replyTo?.id ?? null },
      });
      setDraft("");
      setReplyTo(null);
      await loadMessages();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing || !editing.body.trim()) return;
    try {
      await api<Message>(`/projects/${project.id}/messages/${editing.id}`, {
        method: "PATCH",
        json: { body: editing.body.trim() },
      });
      setEditing(null);
      await loadMessages();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function remove(message: Message) {
    if (!window.confirm("Delete this message? Replies are kept.")) return;
    try {
      await api<Message>(`/projects/${project.id}/messages/${message.id}`, { method: "DELETE" });
      await loadMessages();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function createChannel(event: FormEvent) {
    event.preventDefault();
    if (!newChannel.trim()) return;
    try {
      const created = await api<Channel>(`/projects/${project.id}/channels`, {
        method: "POST",
        json: { name: newChannel.trim() },
      });
      setNewChannel("");
      setChannelId(created.id);
      await loadChannels();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  function renderMessage(message: Message, isReply: boolean) {
    const mine = message.author_user_id === user?.id;
    const canDelete = mine || isModerator;
    return (
      <li key={message.id} className={`${isReply ? "ml-6 border-l-2 border-stone-200 pl-3" : ""} py-2`}>
        <div className="flex flex-wrap items-baseline gap-2 text-xs text-stone-500">
          <span className="font-medium text-stone-800">
            {message.author_agent_handle ? `@${message.author_agent_handle}` : (message.author_display_name ?? "Former member")}
          </span>
          {message.author_agent_handle && (
            <span className="rounded bg-violet-100 px-1.5 py-0.5 text-violet-900">agent · {message.author_display_name} owns it</span>
          )}
          <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
          {message.edited_at && !message.deleted_at && <span>(edited)</span>}
        </div>
        {editing?.id === message.id ? (
          <form onSubmit={saveEdit} className="mt-1 flex gap-2">
            <Input
              aria-label="Edit message"
              value={editing.body}
              onChange={(e) => setEditing({ id: message.id, body: e.target.value })}
            />
            <Button type="submit">Save</Button>
            <Button variant="ghost" type="button" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </form>
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-sm text-stone-900">
            <Body text={message.body} />
          </p>
        )}
        {!message.deleted_at && (
          <div className="mt-1 flex gap-3 text-xs">
            {canWrite && (
              <button className="text-amber-800 hover:underline" onClick={() => setReplyTo(message)}>
                Reply
              </button>
            )}
            {mine && (
              <button
                className="text-stone-600 hover:underline"
                onClick={() => setEditing({ id: message.id, body: message.body })}
              >
                Edit
              </button>
            )}
            {canDelete && (
              <button className="text-red-700 hover:underline" onClick={() => void remove(message)}>
                Delete
              </button>
            )}
          </div>
        )}
      </li>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
      <Card className="h-fit">
        <h2 className="mb-2 text-sm font-semibold text-stone-700">Channels</h2>
        <ul className="space-y-1">
          {channels.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => {
                  setChannelId(c.id);
                  setReplyTo(null);
                }}
                aria-current={c.id === channelId ? "true" : undefined}
                className={`w-full rounded px-2 py-1 text-left text-sm ${c.id === channelId ? "bg-amber-100 text-amber-900" : "text-stone-700 hover:bg-stone-100"}`}
              >
                # {c.name}
              </button>
            </li>
          ))}
        </ul>
        {canWrite && (
          <form onSubmit={createChannel} className="mt-3 space-y-2">
            <Label htmlFor="new-channel">New channel</Label>
            <Input
              id="new-channel"
              value={newChannel}
              onChange={(e) => setNewChannel(e.target.value)}
              placeholder="e.g. product"
            />
            <Button variant="secondary" type="submit" className="w-full">
              Create
            </Button>
          </form>
        )}
      </Card>

      <section className="flex min-h-[60vh] flex-col gap-3">
        <div>
          <h2 className="text-lg font-semibold">{activeChannel ? `# ${activeChannel.name}` : "No channel"}</h2>
          {activeChannel?.topic && <p className="text-sm text-stone-600">{activeChannel.topic}</p>}
        </div>
        <ErrorText>{error}</ErrorText>
        <Card className="flex-1 overflow-y-auto">
          {threads.roots.length === 0 && <EmptyState>No messages yet. Start the conversation.</EmptyState>}
          <ul className="divide-y divide-stone-100">
            {threads.roots.map((root) => (
              <li key={root.id}>
                <ul>{renderMessage(root, false)}</ul>
                {(threads.replies.get(root.id) ?? []).map((reply) => (
                  <ul key={reply.id}>{renderMessage(reply, true)}</ul>
                ))}
              </li>
            ))}
          </ul>
        </Card>
        {canWrite ? (
          <form onSubmit={send} className="space-y-2">
            {replyTo && (
              <p className="flex items-center justify-between rounded bg-stone-100 px-3 py-1 text-xs text-stone-700">
                Replying to {replyTo.author_display_name ?? "a message"}
                <button type="button" className="text-amber-800 hover:underline" onClick={() => setReplyTo(null)}>
                  Cancel reply
                </button>
              </p>
            )}
            <Label htmlFor="composer">Message</Label>
            <TextArea
              id="composer"
              rows={3}
              maxLength={10000}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Write a message. Mention people or agents with @handle."
            />
            <div className="flex justify-end">
              <Button type="submit" disabled={!draft.trim() || !channelId}>
                Send
              </Button>
            </div>
          </form>
        ) : (
          <p className="text-sm text-stone-500">You have view-only access to this project.</p>
        )}
      </section>
    </div>
  );
}
