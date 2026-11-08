import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { api, describeError } from "@/api/client";
import type { BlockedField, ReviewAction, ReviewTaskDetail } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { Chip } from "@/components/Chip";
import { LockBanner } from "@/components/LockBanner";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { confirmAsync, notify } from "@/lib/confirm";
import { kindLabel, shortHash } from "@/materials";
import { reviewActionCopy, type ReviewActionName } from "@/safety";
import { stageColor, useTheme } from "@/theme";
import { formatDateTime, formatScore, humanize } from "@/utils/format";

function callReviewAction(name: ReviewActionName, id: string) {
  switch (name) {
    case "approve":
      return api.approveTask(id);
    case "reject":
      return api.rejectTask(id);
    default:
      return api.resolveTask(id);
  }
}

function useReviewActions(refresh: () => Promise<void>, task?: ReviewTaskDetail) {
  const [busy, setBusy] = useState<string>();
  const [result, setResult] = useState<ReviewAction>();
  const [actionError, setActionError] = useState<string>();

  async function run(name: ReviewActionName) {
    if (!task) return;
    const copy = reviewActionCopy(name);
    const body = name === "approve" && task.approve_effect ? task.approve_effect : copy.body;
    if (!(await confirmAsync(copy.title, body, copy.confirm, name === "reject"))) return;
    setBusy(name);
    setActionError(undefined);
    try {
      setResult(await callReviewAction(name, task.id));
      await refresh();
    } catch (err) {
      setActionError(describeError(err));
    } finally {
      setBusy(undefined);
    }
  }

  async function runAutonomous(jobId: string) {
    setBusy("autonomous");
    setActionError(undefined);
    try {
      const res = await api.runJobAutonomous(jobId);
      notify("Autonomous Run Completed", `Status: ${res.status}. Submitted: ${res.submitted ? "YES" : "NO"}`);
      await refresh();
    } catch (err) {
      setActionError(describeError(err));
    } finally {
      setBusy(undefined);
    }
  }

  return { busy, result, actionError, run, runAutonomous };
}

function TaskHeader({ task }: { task: ReviewTaskDetail }) {
  const theme = useTheme();
  const open = task.status === "open";
  return (
    <View style={styles.headerContainer}>
      <View style={styles.badges}>
        <Badge label={humanize(task.reason_code)} color={open ? theme.warning : theme.textFaint} />
        <Badge label={humanize(task.status)} color={open ? theme.info : theme.textMuted} />
        {open ? <Badge label="Interactive Review" color={theme.primary} /> : null}
      </View>
      <Text style={[styles.title, { color: theme.text }]}>{task.title}</Text>
      <Text style={[styles.meta, { color: theme.textMuted }]}>Opened {formatDateTime(task.created_at)}</Text>
    </View>
  );
}

function getSuggestionChips(key: string, label: string): string[] {
  const text = `${key} ${label}`.toLowerCase();
  if (text.includes("sponsor") || text.includes("authoriz") || text.includes("legally") || text.includes("citizen") || text.includes("visa")) {
    return ["Canadian Citizen", "Authorized without sponsorship", "No sponsorship required", "Yes"];
  }
  if (text.includes("relocat") || text.includes("commute") || text.includes("hybrid") || text.includes("ottawa") || text.includes("location") || text.includes("remote")) {
    return ["Ottawa, ON, Canada", "Hybrid (Ottawa NCR)", "Remote Canada", "Yes"];
  }
  if (text.includes("gender") || text.includes("race") || text.includes("veteran") || text.includes("disabilit") || text.includes("demographic") || text.includes("eeoc") || text.includes("voluntary")) {
    return ["Decline to self-identify", "Prefer not to answer"];
  }
  if (text.includes("experience") || text.includes("year")) {
    return ["5+ years", "3-5 years", "Yes"];
  }
  if (text.includes("notice") || text.includes("start")) {
    return ["Immediately", "2 weeks notice", "Negotiable"];
  }
  return ["Yes", "No", "N/A"];
}

function InteractiveBlockedFieldsCard({
  task,
  fields,
  onRevalidated,
}: {
  task: ReviewTaskDetail;
  fields: BlockedField[];
  onRevalidated: () => Promise<void>;
}) {
  const theme = useTheme();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saveToVault, setSaveToVault] = useState<Record<string, boolean>>({});
  const [savingKeys, setSavingKeys] = useState<Record<string, boolean>>({});
  const [savedKeys, setSavedKeys] = useState<Record<string, string>>({});
  const [revalidating, setRevalidating] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const effectiveFields: BlockedField[] =
    fields.length > 0
      ? fields
      : ["ambiguous_question", "legal_answer_missing", "sensitive_answer_missing", "unsupported_control"].includes(task.reason_code)
        ? [
            {
              key: task.title.replace(/^Answer required.*?:\s*/i, "").toLowerCase().replace(/[^a-z0-9_]+/g, "_") || "custom_question",
              label: task.title,
              section: "application",
              required: true,
              reason: task.detail || humanize(task.reason_code),
            },
          ]
        : [];

  if (!effectiveFields.length) return null;

  async function handleSaveAnswer(fieldKey: string, fieldLabel: string) {
    const val = (answers[fieldKey] || "").trim();
    if (!val) {
      notify("Answer required", "Please type or pick an answer first.");
      return;
    }
    setSavingKeys((prev) => ({ ...prev, [fieldKey]: true }));
    try {
      await api.saveAnswer({
        key: fieldKey,
        value: val,
        scope: saveToVault[fieldKey] !== false ? "global" : "job",
        source: "user",
        note: `Answered in mobile review for ${fieldLabel || fieldKey}`,
      });
      setSavedKeys((prev) => ({ ...prev, [fieldKey]: val }));
      notify("Answer Saved", `Saved "${val}" for ${fieldLabel || fieldKey} in Answer Vault.`);
    } catch (err) {
      notify("Failed to save", describeError(err));
    } finally {
      setSavingKeys((prev) => ({ ...prev, [fieldKey]: false }));
    }
  }

  async function handleRecheckAll() {
    setRevalidating(true);
    setFeedback(null);
    try {
      const res = await api.revalidateReviews([task.id]);
      const resolvedCount = res.resolved ?? res.revalidated ?? 0;
      if (resolvedCount > 0) {
        setFeedback("✓ Task resolved! Application automatically promoted to Ready to Apply.");
        notify("Review Resolved", "All questions satisfied against Vault. Job promoted to Ready to Apply!");
      } else {
        setFeedback("Rechecked against Vault. Save an answer above or click Mark as Resolved below.");
      }
      await onRevalidated();
    } catch (err) {
      notify("Revalidation failed", describeError(err));
    } finally {
      setRevalidating(false);
    }
  }

  return (
    <Card
      title="Interactive Question Studio (JobTomatik)"
      accentColor={theme.warning}
      right={
        <Badge
          label={`${effectiveFields.length} Need Answer`}
          color={theme.warning}
        />
      }
    >
      <Muted>
        Answer questions directly below. You can pick suggested quick answers or type custom text. Saved answers persist to your Answer Vault for future jobs.
      </Muted>

      {feedback ? (
        <Banner tone="info" message={feedback} />
      ) : null}

      <View style={{ gap: 16, marginTop: 4 }}>
        {effectiveFields.map((field) => {
          const suggestions = getSuggestionChips(field.key, field.label || "");
          const currentVal = answers[field.key] ?? "";
          const isSaving = savingKeys[field.key];
          const isSaved = savedKeys[field.key];
          const shouldVault = saveToVault[field.key] !== false;

          return (
            <View
              key={field.key}
              style={[
                styles.questionBox,
                {
                  backgroundColor: theme.surfaceAlt,
                  borderColor: isSaved ? theme.success : theme.border,
                },
              ]}
            >
              <View style={styles.questionHeader}>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.fieldLabel, { color: theme.text }]}>
                    {field.label || field.key}
                    {field.required ? " *" : ""}
                  </Text>
                  <Muted>{humanize(field.section)} · {field.reason}</Muted>
                </View>
                {isSaved ? (
                  <Badge label="Saved in Vault" color={theme.success} />
                ) : null}
              </View>

              <TextInput
                style={[
                  styles.input,
                  {
                    color: theme.text,
                    backgroundColor: theme.surface,
                    borderColor: theme.border,
                  },
                ]}
                value={currentVal}
                placeholder="Type or select an answer..."
                placeholderTextColor={theme.textFaint}
                onChangeText={(text) => {
                  setAnswers((prev) => ({ ...prev, [field.key]: text }));
                }}
              />

              <View style={styles.chipsRow}>
                {suggestions.map((suggestion) => (
                  <Chip
                    key={suggestion}
                    label={suggestion}
                    selected={currentVal === suggestion}
                    onPress={() => {
                      setAnswers((prev) => ({ ...prev, [field.key]: suggestion }));
                    }}
                  />
                ))}
              </View>

              <View style={styles.vaultToggleRow}>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.vaultToggleText, { color: theme.text }]}>
                    Save to Answer Vault
                  </Text>
                  <Muted>Auto-answer this for all future applications</Muted>
                </View>
                <Switch
                  value={shouldVault}
                  onValueChange={(val) => {
                    setSaveToVault((prev) => ({ ...prev, [field.key]: val }));
                  }}
                />
              </View>

              <PrimaryButton
                title={isSaving ? "Saving to Vault..." : isSaved ? "✓ Update Vault Answer" : "Save Answer to Vault"}
                variant={isSaved ? "secondary" : "primary"}
                loading={isSaving}
                onPress={() => void handleSaveAnswer(field.key, field.label || field.key)}
              />
            </View>
          );
        })}
      </View>

      <View style={{ marginTop: 8 }}>
        <PrimaryButton
          title={revalidating ? "Checking Vault against Form..." : "⚡ Recheck All Answers & Resolve Review"}
          variant="primary"
          loading={revalidating}
          onPress={() => void handleRecheckAll()}
        />
      </View>
    </Card>
  );
}

function TaskJobCard({ task }: { task: ReviewTaskDetail }) {
  const theme = useTheme();
  const { job, application, url } = task;
  if (!job) return null;
  return (
    <Card title="Job Details">
      <Link href={{ pathname: "/jobs/[id]", params: { id: job.id } }} style={StyleSheet.flatten([styles.link, { color: theme.primary }])}>
        {job.title} · {job.company} ›
      </Link>
      <Row label="Pipeline Stage" value={humanize(job.stage)} valueColor={stageColor(theme, job.stage)} />
      <Row label="Match Score" value={formatScore(job.score)} />
      {application ? <Row label="Application State" value={humanize(application.stage)} /> : null}
      {url ? <PrimaryButton title="Open Job Posting in Browser" variant="secondary" onPress={() => void Linking.openURL(url)} /> : null}
    </Card>
  );
}

function TaskMaterialsCard({ task }: { task: ReviewTaskDetail }) {
  const theme = useTheme();
  const router = useRouter();
  const { job } = task;
  if (!job || (!task.materials.length && task.reason_code !== "materials_review")) return null;
  return (
    <Card title="Tailored Application Materials">
      {task.materials.map((item) => (
        <Row
          key={item.id}
          label={`${kindLabel(item.kind)} v${item.version}`}
          value={`${item.status} · ${shortHash(item.content_sha256)}`}
          valueColor={item.status === "approved" ? theme.success : theme.warning}
        />
      ))}
      <Muted>Approving this task approves tailored drafts as-is. Factual Truth Guard verified.</Muted>
      <PrimaryButton title="Preview & Tailor Materials ›" variant="secondary" onPress={() => {
        router.push({ pathname: "/materials/[id]", params: { id: job.id } });
      }} />
    </Card>
  );
}

type ActionsProps = Pick<ReturnType<typeof useReviewActions>, "busy" | "run" | "runAutonomous"> & { task: ReviewTaskDetail };

function ActionsCard({ task, busy, run, runAutonomous }: ActionsProps) {
  const theme = useTheme();
  if (task.status !== "open") return <Muted>Closed {formatDateTime(task.resolved_at)}.</Muted>;

  const isUncertain = task.reason_code === "submission_confirmation_uncertain";

  return (
    <Card title="Review Actions" accentColor={isUncertain ? theme.uncertain : theme.primary}>
      {isUncertain ? (
        <View style={{ gap: 8, marginBottom: 6 }}>
          <Muted>
            ⚠️ Duplicate Submission Safeguard: Confirm whether the employer received this application before re-queuing.
          </Muted>
          <PrimaryButton
            title="✅ Verified NOT Received — Clear Hold & Re-Queue"
            variant="success"
            loading={busy === "resolve"}
            disabled={!!busy}
            onPress={() => void run("resolve")}
          />
        </View>
      ) : null}
      {task.approve_effect ? <Muted>Approve action: {task.approve_effect}</Muted> : null}
      <View style={styles.actions}>
        {task.actions.includes("approve") ? (
          <PrimaryButton title="Approve Application" loading={busy === "approve"} disabled={!!busy} onPress={() => void run("approve")} />
        ) : null}
        {task.job && !isUncertain ? (
          <PrimaryButton
            title="⚡ Run Autonomous Apply Now"
            variant="primary"
            loading={busy === "autonomous"}
            disabled={!!busy}
            onPress={() => void runAutonomous(task.job!.id)}
          />
        ) : null}
        {task.actions.includes("reject") ? (
          <PrimaryButton title="Reject Application" variant="danger" loading={busy === "reject"} disabled={!!busy} onPress={() => void run("reject")} />
        ) : null}
        {!isUncertain ? (
          <PrimaryButton title="Mark as Resolved" variant="secondary" loading={busy === "resolve"} disabled={!!busy} onPress={() => void run("resolve")} />
        ) : null}
      </View>
    </Card>
  );
}

export default function ReviewTaskScreen() {
  const theme = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: task, loading, refreshing, error, refresh, reload } = useApiResource(() => api.reviewTask(String(id)), [id]);
  const { busy, result, actionError, run, runAutonomous } = useReviewActions(refresh, task ?? undefined);

  if (loading && !task) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!task) return <ScreenContainer><ErrorView message={error ?? "Task not found."} onRetry={reload} /></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}
        <TaskHeader task={task} />
        {task.detail ? (
          <Card title="Review Reason & Details">
            <Text style={[styles.detail, { color: theme.text }]}>{task.detail}</Text>
          </Card>
        ) : null}
        <InteractiveBlockedFieldsCard
          task={task}
          fields={task.blocked_fields}
          onRevalidated={refresh}
        />
        <TaskMaterialsCard task={task} />
        <TaskJobCard task={task} />
        {result ? (
          <Banner tone="info" message={`Done: ${humanize(result.action)}. Job is now ${humanize(result.job_stage)}.`} />
        ) : null}
        {actionError ? <Banner tone="danger" message={actionError} /> : null}
        <ActionsCard task={task} busy={busy} run={run} runAutonomous={runAutonomous} />
        <LockBanner />
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  headerContainer: { gap: 6 },
  badges: { flexDirection: "row", gap: 6, flexWrap: "wrap" },
  title: { fontSize: 20, fontWeight: "800", letterSpacing: -0.3 },
  meta: { fontSize: 13 },
  detail: { fontSize: 14, lineHeight: 21 },
  questionBox: { borderRadius: 12, borderWidth: 1, padding: 12, gap: 10 },
  questionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 8 },
  fieldLabel: { fontSize: 14, fontWeight: "700" },
  input: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  vaultToggleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingTop: 4 },
  vaultToggleText: { fontSize: 13, fontWeight: "600" },
  link: { fontSize: 15, fontWeight: "700" },
  actions: { gap: 10 },
});
