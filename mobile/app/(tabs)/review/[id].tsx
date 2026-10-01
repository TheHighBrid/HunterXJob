import { Link, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { Linking, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { BlockedField, ReviewAction, ReviewTaskDetail } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { LockBanner } from "@/components/LockBanner";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { confirmAsync } from "@/lib/confirm";
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
  const [busy, setBusy] = useState<ReviewActionName>();
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
  return { busy, result, actionError, run };
}

function TaskHeader({ task }: { task: ReviewTaskDetail }) {
  const theme = useTheme();
  const open = task.status === "open";
  return (
    <View style={{ gap: 6 }}>
      <View style={styles.badges}>
        <Badge label={humanize(task.reason_code)} color={open ? theme.warning : theme.textFaint} />
        <Badge label={humanize(task.status)} color={open ? theme.info : theme.textMuted} />
      </View>
      <Text style={[styles.title, { color: theme.text }]}>{task.title}</Text>
      <Text style={[styles.meta, { color: theme.textMuted }]}>Opened {formatDateTime(task.created_at)}</Text>
    </View>
  );
}

function BlockedFieldsCard({ fields }: { fields: BlockedField[] }) {
  const theme = useTheme();
  if (!fields.length) return null;
  return (
    <Card title="Questions that need you">
      {fields.map((field) => (
        <View key={field.key} style={[styles.field, { borderColor: theme.border }]}>
          <Text style={[styles.fieldLabel, { color: theme.text }]}>
            {field.label || field.key}
            {field.required ? " *" : ""}
          </Text>
          <Muted>{humanize(field.section)} · {field.reason}</Muted>
        </View>
      ))}
      <Muted>Add the answers on the server (answer vault), then approve to queue a new dry-run.</Muted>
    </Card>
  );
}

function TaskJobCard({ task }: { task: ReviewTaskDetail }) {
  const theme = useTheme();
  const { job, application, url } = task;
  if (!job) return null;
  return (
    <Card title="Job">
      <Link href={{ pathname: "/jobs/[id]", params: { id: job.id } }} style={StyleSheet.flatten([styles.link, { color: theme.primary }])}>
        {job.title} · {job.company} ›
      </Link>
      <Row label="Stage" value={humanize(job.stage)} valueColor={stageColor(theme, job.stage)} />
      <Row label="Score" value={formatScore(job.score)} />
      {application ? <Row label="Application" value={humanize(application.stage)} /> : null}
      {url ? <PrimaryButton title="Open posting" variant="secondary" onPress={() => void Linking.openURL(url)} /> : null}
    </Card>
  );
}

type ActionsProps = Pick<ReturnType<typeof useReviewActions>, "busy" | "run"> & { task: ReviewTaskDetail };

function ActionsCard({ task, busy, run }: ActionsProps) {
  if (task.status !== "open") return <Muted>Closed {formatDateTime(task.resolved_at)}.</Muted>;
  return (
    <Card title="Actions">
      {task.approve_effect ? <Muted>Approve: {task.approve_effect}</Muted> : null}
      <View style={styles.actions}>
        {task.actions.includes("approve") ? (
          <PrimaryButton title="Approve" loading={busy === "approve"} disabled={!!busy} onPress={() => void run("approve")} />
        ) : null}
        {task.actions.includes("reject") ? (
          <PrimaryButton title="Reject" variant="danger" loading={busy === "reject"} disabled={!!busy} onPress={() => void run("reject")} />
        ) : null}
        <PrimaryButton title="Mark resolved" variant="secondary" loading={busy === "resolve"} disabled={!!busy} onPress={() => void run("resolve")} />
      </View>
    </Card>
  );
}

export default function ReviewTaskScreen() {
  const theme = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: task, loading, refreshing, error, refresh, reload } = useApiResource(() => api.reviewTask(String(id)), [id]);
  const { busy, result, actionError, run } = useReviewActions(refresh, task ?? undefined);

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
          <Card title="Details">
            <Text style={[styles.detail, { color: theme.text }]}>{task.detail}</Text>
          </Card>
        ) : null}
        <BlockedFieldsCard fields={task.blocked_fields} />
        <TaskJobCard task={task} />
        {result ? (
          <Banner tone="info" message={`Done: ${humanize(result.action)}. Job is now ${humanize(result.job_stage)}. Nothing was submitted.`} />
        ) : null}
        {actionError ? <Banner tone="danger" message={actionError} /> : null}
        <ActionsCard task={task} busy={busy} run={run} />
        <LockBanner />
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  badges: { flexDirection: "row", gap: 6, flexWrap: "wrap" },
  title: { fontSize: 20, fontWeight: "800" },
  meta: { fontSize: 13 },
  detail: { fontSize: 14, lineHeight: 20 },
  field: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 6, gap: 2 },
  fieldLabel: { fontSize: 14, fontWeight: "600" },
  link: { fontSize: 15, fontWeight: "700" },
  actions: { gap: 10 },
});
