import { useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { FormPreview, FormStatus, JobDetail } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { matchScoreColor, stageColor, useTheme } from "@/theme";
import { formatDateTime, formatScore, humanize } from "@/utils/format";

const FORM_STATE_COPY: Record<FormStatus["state"], string> = {
  no_application: "Not shortlisted yet, so no form has been checked.",
  not_checked: "The application form hasn't been dry-run yet.",
  validated: "Dry-run passed: every field was resolved. Nothing was submitted.",
  unavailable: "The real form couldn't be loaded. See the review task.",
  blocked: "The dry-run stopped: some answers need you.",
  in_progress: "A dry-run started but hasn't finished.",
};

function ScoreBlock({ job }: { job: JobDetail }) {
  const theme = useTheme();
  const items: [string, number | null | undefined][] = [
    ["Final", job.scores.final],
    ["Rules", job.scores.deterministic],
    ["AI", job.scores.ai],
  ];
  return (
    <View style={styles.scores}>
      {items.map(([label, value]) => (
        <View key={label} style={[styles.scoreBox, { borderColor: theme.border, backgroundColor: theme.surfaceAlt }]}>
          <Text style={[styles.scoreValue, { color: matchScoreColor(theme, value) }]}>{formatScore(value)}</Text>
          <Text style={[styles.scoreLabel, { color: theme.textMuted }]}>{label}</Text>
        </View>
      ))}
    </View>
  );
}

function FormStatusCard({ status, preview }: { status: FormStatus; preview?: FormPreview }) {
  const theme = useTheme();
  const color = status.state === "validated" ? theme.success : status.state === "blocked" || status.state === "unavailable" ? theme.warning : theme.textMuted;
  return (
    <Card title="Application form" right={<Badge label={humanize(status.state)} color={color} />}>
      <Muted>{FORM_STATE_COPY[status.state]}</Muted>
      {status.source ? <Row label="Source" value={humanize(status.source)} /> : null}
      {status.fields_total != null ? <Row label="Fields" value={`${status.fields_total} total · ${status.fields_required ?? "?"} required`} /> : null}
      {status.fields_filled != null ? <Row label="Resolved" value={String(status.fields_filled)} valueColor={theme.success} /> : null}
      {status.blockers.length ? <Row label="Blockers" value={status.blockers.map(humanize).join(", ")} valueColor={theme.warning} /> : null}
      {status.blocked_fields.map((field) => (
        <View key={field.key} style={[styles.blocked, { borderColor: theme.border }]}>
          <Text style={[styles.blockedLabel, { color: theme.text }]}>
            {field.label || field.key}
            {field.required ? " *" : ""}
          </Text>
          <Text style={[styles.blockedReason, { color: theme.textMuted }]}>
            {humanize(field.section)} · {field.reason}
          </Text>
        </View>
      ))}
      {status.warnings.length ? <Muted>Warnings: {status.warnings.join("; ")}</Muted> : null}
      {status.last_error ? <Banner tone="warning" message={status.last_error} /> : null}
      {preview ? (
        <View style={{ gap: 6 }}>
          <Text style={[styles.blockedLabel, { color: theme.text }]}>
            Live check: {preview.fields.length} fields, {preview.fields.filter((f) => f.status === "fill").length} resolvable
            {preview.ready ? " — ready" : ` — blocked by ${preview.blockers.map(humanize).join(", ") || "review"}`}
          </Text>
          {preview.fields.filter((f) => f.status !== "fill").slice(0, 12).map((f) => (
            <Muted key={f.key}>• {f.label || f.key}: {f.reason}</Muted>
          ))}
        </View>
      ) : null}
    </Card>
  );
}

function JobHeader({ job }: { job: JobDetail }) {
  const theme = useTheme();
  return (
    <View style={{ gap: 6 }}>
      <Text style={[styles.title, { color: theme.text }]}>{job.title}</Text>
      <Text style={[styles.meta, { color: theme.textMuted }]}>
        {job.company} · {job.location || "—"}
        {job.remote ? " · Remote" : ""}
      </Text>
      <View style={styles.badges}>
        <Badge label={humanize(job.stage)} color={stageColor(theme, job.stage)} />
        {job.platform ? <Badge label={job.platform} color={theme.info} /> : null}
      </View>
    </View>
  );
}

function ScoreCard({ job }: { job: JobDetail }) {
  const decision = job.decision;
  return (
    <Card title="Score">
      <ScoreBlock job={job} />
      {job.reason ? <Muted>{job.reason}</Muted> : null}
      {decision ? (
        <View style={{ gap: 6 }}>
          {decision.dimensions.map((dimension) => (
            <Row
              key={dimension.name}
              label={humanize(dimension.name)}
              value={`${Math.round(dimension.score)} × ${dimension.weight} = ${dimension.weighted_points.toFixed(1)}`}
            />
          ))}
          {decision.matched_keywords.length ? <Muted>Matched: {decision.matched_keywords.join(", ")}</Muted> : null}
          {decision.review_flags.length ? <Muted>Review flags: {decision.review_flags.join(", ")}</Muted> : null}
          {decision.vetoes.length ? <Muted>Vetoes: {decision.vetoes.join(", ")}</Muted> : null}
        </View>
      ) : null}
    </Card>
  );
}

function OpenTasksCard({ tasks }: { tasks: JobDetail["review_tasks"] }) {
  const theme = useTheme();
  const router = useRouter();
  if (!tasks.length) return null;
  return (
    <Card title={`Open review tasks (${tasks.length})`}>
      {tasks.map((task) => (
        <Pressable
          key={task.id}
          accessibilityRole="button"
          onPress={() => {
            router.push({ pathname: "/review/[id]", params: { id: task.id } });
          }}
          style={({ pressed }) => [styles.task, { borderColor: theme.border, opacity: pressed ? 0.7 : 1 }]}
        >
          <Text style={[styles.blockedLabel, { color: theme.text }]}>{task.title}</Text>
          <Text style={[styles.blockedReason, { color: theme.warning }]}>{humanize(task.reason_code)} ›</Text>
        </Pressable>
      ))}
    </Card>
  );
}

function ApplicationCard({ application, jobId }: { application: JobDetail["application"]; jobId: string }) {
  const theme = useTheme();
  const router = useRouter();
  if (!application) return null;
  return (
    <Card title="Application">
      <Row label="Stage" value={humanize(application.stage)} valueColor={stageColor(theme, application.stage)} />
      <Row label="Mode" value={humanize(application.mode)} />
      <Row label="Adapter" value={`${application.adapter ?? "—"} (${humanize(application.maturity)})`} />
      <Row label="Attempts" value={String(application.attempts)} />
      <Row label="Approved materials" value={application.materials_ready ? "Résumé approved" : "Not yet"} valueColor={application.materials_ready ? theme.success : theme.textMuted} />
      <Row label="Cover letter" value={application.has_cover_letter ? "Approved" : "None approved"} />
      <PrimaryButton title="Résumé & cover letter ›" variant="secondary" onPress={() => {
        router.push({ pathname: "/materials/[id]", params: { id: jobId } });
      }} />
    </Card>
  );
}

function TimelineCard({ events }: { events: JobDetail["events"] }) {
  const theme = useTheme();
  return (
    <Card title="Timeline">
      {events.length === 0 ? <Muted>No events.</Muted> : null}
      {events.slice(0, 15).map((event) => (
        <View key={`${event.created_at}-${event.from_stage ?? ""}-${event.to_stage}`} style={styles.event}>
          <Text style={[styles.eventStage, { color: stageColor(theme, event.to_stage) }]}>{humanize(event.to_stage)}</Text>
          <Text style={[styles.blockedReason, { color: theme.textMuted }]}>
            {formatDateTime(event.created_at)} · {event.message}
          </Text>
        </View>
      ))}
    </Card>
  );
}

function DescriptionCard({ description }: { description: string | null | undefined }) {
  const theme = useTheme();
  if (!description) return null;
  return (
    <Card title="Description">
      <Text style={[styles.description, { color: theme.text }]} numberOfLines={30}>
        {description.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()}
      </Text>
    </Card>
  );
}

function useFormPreview(id: string) {
  const [preview, setPreview] = useState<FormPreview>();
  const [previewError, setPreviewError] = useState<string>();
  const [checking, setChecking] = useState(false);
  async function checkForm() {
    setChecking(true);
    setPreviewError(undefined);
    try {
      setPreview(await api.previewForm(id));
    } catch (err) {
      setPreviewError(describeError(err));
    } finally {
      setChecking(false);
    }
  }
  return { preview, previewError, checking, checkForm };
}

export default function JobDetailScreen() {
  const theme = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: job, loading, refreshing, error, refresh, reload } = useApiResource(() => api.job(String(id)), [id]);
  const { preview, previewError, checking, checkForm } = useFormPreview(String(id));

  if (loading && !job) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!job) return <ScreenContainer><ErrorView message={error ?? "Job not found."} onRetry={reload} /></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}
        <JobHeader job={job} />
        <ScoreCard job={job} />
        <FormStatusCard status={job.form_status} preview={preview} />
        {previewError ? <Banner tone="danger" message={previewError} /> : null}
        <PrimaryButton title="Check the live form now (read-only)" variant="secondary" loading={checking} onPress={() => void checkForm()} />
        <OpenTasksCard tasks={job.review_tasks.filter((task) => task.status === "open")} />
        <ApplicationCard application={job.application} jobId={job.id} />
        <TimelineCard events={job.events} />
        <DescriptionCard description={job.description} />
        <PrimaryButton title="Open posting in browser" variant="secondary" onPress={() => void Linking.openURL(job.url)} />
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  title: { fontSize: 22, fontWeight: "800" },
  meta: { fontSize: 14 },
  badges: { flexDirection: "row", gap: 6, flexWrap: "wrap" },
  scores: { flexDirection: "row", gap: 10 },
  scoreBox: { flex: 1, borderWidth: 1, borderRadius: 12, alignItems: "center", paddingVertical: 10 },
  scoreValue: { fontSize: 24, fontWeight: "800" },
  scoreLabel: { fontSize: 12, fontWeight: "600" },
  blocked: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 6, gap: 2 },
  blockedLabel: { fontSize: 14, fontWeight: "600" },
  blockedReason: { fontSize: 12, lineHeight: 17 },
  task: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 8, gap: 2 },
  event: { gap: 2 },
  eventStage: { fontSize: 13, fontWeight: "700" },
  description: { fontSize: 14, lineHeight: 20 },
});
