import { useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { FormPreview, FormStatus, JobDetail } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { LinkedPostingsCard, LivenessBadge, LivenessCard } from "@/components/DiscoveryCards";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { linkedLabel, livenessBadgeStatus, sourceLabel } from "@/discovery";
import { useApiResource } from "@/hooks/useApiResource";
import { confirmAsync, notify } from "@/lib/confirm";
import { useSubscription, MAX_DAILY_FREE_APPLICATIONS } from "@/store/subscription";
import { matchScoreColor, stageColor, useTheme } from "@/theme";
import { formatDateTime, formatScore, humanize } from "@/utils/format";

const FORM_STATE_COPY: Record<FormStatus["state"], string> = {
  no_application: "Not shortlisted yet, so no form has been checked.",
  not_checked: "The application form hasn't been dry-run yet.",
  validated: "Dry-run verified: every required field resolved cleanly from your Profile & Answer Vault.",
  unavailable: "The real form couldn't be loaded automatically. See the review task or use manual handoff.",
  blocked: "Form pre-check paused: one or more questions require your answer in Review Studio.",
  in_progress: "A form check started and is currently in progress.",
  submission_uncertain:
    "Hold active: A live submission was attempted, but post-submit confirmation was not verified on the ATS page (e.g., CAPTCHA challenge). Verify before retrying to prevent duplicate applications.",
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

function FormStatusCard({
  status,
  preview,
  openTaskId,
}: {
  status: FormStatus;
  preview?: FormPreview;
  openTaskId?: string;
}) {
  const theme = useTheme();
  const router = useRouter();
  const color =
    status.state === "validated"
      ? theme.success
      : status.state === "submission_uncertain"
        ? theme.uncertain
        : status.state === "blocked" || status.state === "unavailable"
          ? theme.warning
          : theme.textMuted;
  return (
    <Card
      title="Application Form Status"
      accentColor={color}
      right={<Badge label={humanize(status.state)} color={color} />}
    >
      <Muted>{FORM_STATE_COPY[status.state]}</Muted>
      {status.source ? <Row label="Source" value={humanize(status.source)} /> : null}
      {status.fields_total != null ? <Row label="Fields" value={`${status.fields_total} total · ${status.fields_required ?? "?"} required`} /> : null}
      {status.fields_filled != null ? <Row label="Resolved" value={String(status.fields_filled)} valueColor={theme.success} /> : null}
      {status.blockers.length ? <Row label="Blockers" value={status.blockers.map(humanize).join(", ")} valueColor={color} /> : null}
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
      {status.state === "blocked" || status.blocked_fields.length > 0 ? (
        <View style={{ marginTop: 8 }}>
          <PrimaryButton
            title={openTaskId ? "👉 Answer Questions in Review Studio ›" : "👉 Go to Review Studio ›"}
            variant="warning"
            onPress={() => {
              if (openTaskId) {
                router.push({ pathname: "/review/[id]", params: { id: openTaskId } });
              } else {
                router.push({ pathname: "/(tabs)/review" });
              }
            }}
          />
        </View>
      ) : null}
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

function ExecutionCommandCard({
  job,
  openTasks,
  refresh,
}: {
  job: JobDetail;
  openTasks: JobDetail["review_tasks"];
  refresh: () => Promise<void>;
}) {
  const theme = useTheme();
  const router = useRouter();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [execBanner, setExecBanner] = useState<{ tone: "info" | "warning" | "danger"; text: string } | null>(null);

  const isUncertain =
    job.stage === "submission_uncertain" ||
    job.application?.stage === "submission_uncertain" ||
    job.form_status.state === "submission_uncertain";
  const isSubmitted =
    job.stage === "submitted" ||
    job.stage === "confirmed" ||
    job.application?.stage === "submitted" ||
    job.application?.stage === "confirmed";
  const isNeedsReview =
    job.stage === "needs_review" ||
    job.stage === "review" ||
    job.form_status.state === "blocked";

  const uncertainTask = openTasks.find((t) => t.reason_code === "submission_confirmation_uncertain") ?? openTasks[0];

  async function handleRunAutonomous(dryRun: boolean) {
    setBusyAction(dryRun ? "dry_run" : "submit_live");
    setExecBanner(null);
    try {
      const res = await api.runJobAutonomous(job.id, dryRun);
      await refresh();
      if (res.submitted) {
        setExecBanner({
          tone: "info",
          text: `✓ Live application submitted on ${res.platform}! Evidence SHA256: ${(res.evidence_sha256 || "").slice(0, 12)}`,
        });
        notify("Application Submitted 🎉", `Submitted to ${job.company} (${res.platform}).`);
      } else if (res.status === "dry_run_verified") {
        setExecBanner({
          tone: "info",
          text: `✓ Browser dry-run verified all fields cleanly on ${res.platform}. Stage: ${humanize(res.stage || "ready_to_apply")}.`,
        });
        notify("Dry-Run Verified ✓", `All fields on ${job.company} filled and verified cleanly.`);
      } else {
        setExecBanner({
          tone: "warning",
          text: `Execution finished with status "${humanize(res.status)}"${res.error ? `: ${res.error}` : ""}`,
        });
        notify("Execution Status", `${humanize(res.status)}${res.error ? ` — ${res.error}` : ""}`);
      }
    } catch (err) {
      const msg = describeError(err);
      setExecBanner({ tone: "danger", text: msg });
      notify("Execution Failed", msg);
    } finally {
      setBusyAction(null);
    }
  }

  async function handleClearUncertainHold(resolution: "resolved" | "dismissed") {
    if (!uncertainTask) return;
    setBusyAction(resolution);
    setExecBanner(null);
    try {
      await api.resolveTask(uncertainTask.id, resolution);
      await refresh();
      if (resolution === "resolved") {
        setExecBanner({
          tone: "info",
          text: "✓ Unconfirmed submission hold cleared! Job and Application returned to Ready to Apply.",
        });
        notify("Hold Cleared", "Application returned to Ready to Apply.");
      } else {
        setExecBanner({
          tone: "info",
          text: "Hold dismissed.",
        });
      }
    } catch (err) {
      setExecBanner({ tone: "danger", text: describeError(err) });
    } finally {
      setBusyAction(null);
    }
  }

  async function handlePrepareMaterials() {
    setBusyAction("materials");
    setExecBanner(null);
    try {
      const res = await api.generateMaterials(job.id);
      for (const m of res.items ?? []) {
        if (m.status === "draft") {
          await api.approveMaterial(m.id, "Approved from Job Command Deck");
        }
      }
      await refresh();
      setExecBanner({
        tone: "info",
        text: "✓ Tailored résumé and cover letter generated and approved!",
      });
    } catch (err) {
      setExecBanner({ tone: "danger", text: describeError(err) });
    } finally {
      setBusyAction(null);
    }
  }

  const accent = isSubmitted
    ? theme.success
    : isUncertain
      ? theme.uncertain
      : isNeedsReview
        ? theme.warning
        : theme.primary;

  return (
    <Card
      title="Application Execution & Command Deck"
      accentColor={accent}
      right={<Badge label={humanize(job.stage)} color={accent} />}
    >
      <Row
        label="Synchronized State"
        value={`Job: ${humanize(job.stage)} · App: ${humanize(job.application?.stage ?? job.stage)}`}
        valueColor={accent}
      />
      <Row
        label="Materials & Resume"
        value={job.application?.materials_ready ? "Approved & Ready ✓" : "Needs Generation / Approval"}
        valueColor={job.application?.materials_ready ? theme.success : theme.warning}
      />
      <Row
        label="Form Verification"
        value={humanize(job.form_status.state)}
        valueColor={
          job.form_status.state === "validated"
            ? theme.success
            : job.form_status.state === "submission_uncertain"
              ? theme.uncertain
              : theme.textMuted
        }
      />
      <Row label="Recorded Attempts" value={String(job.application?.attempts ?? 0)} />

      {execBanner ? <Banner tone={execBanner.tone} message={execBanner.text} /> : null}

      {isUncertain ? (
        <View style={[styles.holdBox, { backgroundColor: theme.uncertain + "14", borderColor: theme.uncertain }]}>
          <Text style={[styles.holdTitle, { color: theme.uncertain }]}>
            ⚠️ Unconfirmed Live Submission Hold (Duplicate Protection)
          </Text>
          <Muted>
            {job.application?.last_error ||
              "A headless submission was attempted, but the ATS confirmation page was not verified (e.g., Lever hCaptcha challenge on submit button). Automatic retries are locked until you confirm whether the employer received it."}
          </Muted>
          {uncertainTask ? (
            <View style={{ gap: 8, marginTop: 6 }}>
              <PrimaryButton
                title="✅ Verified Not Received — Clear Hold & Re-Queue"
                variant="success"
                loading={busyAction === "resolved"}
                disabled={!!busyAction}
                onPress={() => void handleClearUncertainHold("resolved")}
              />
              <PrimaryButton
                title="📌 Dismiss Hold (Handled Manually)"
                variant="secondary"
                loading={busyAction === "dismissed"}
                disabled={!!busyAction}
                onPress={() => void handleClearUncertainHold("dismissed")}
              />
            </View>
          ) : null}
        </View>
      ) : null}

      {isNeedsReview && openTasks.length > 0 ? (
        <PrimaryButton
          title={`👉 Answer Blocked Question(s) in Review Studio (${openTasks.length}) ›`}
          variant="warning"
          onPress={() => {
            router.push({ pathname: "/review/[id]", params: { id: openTasks[0].id } });
          }}
        />
      ) : null}

      {!isSubmitted && !isUncertain ? (
        <View style={{ gap: 8, marginTop: 4 }}>
          <PrimaryButton
            title="🚀 Submit Application Now (Autonomous Live)"
            variant="primary"
            loading={busyAction === "submit_live"}
            disabled={!!busyAction}
            onPress={() => void handleRunAutonomous(false)}
          />
          <PrimaryButton
            title="🧪 Run Browser Dry-Run Verification"
            variant="secondary"
            loading={busyAction === "dry_run"}
            disabled={!!busyAction}
            onPress={() => void handleRunAutonomous(true)}
          />
          {!job.application?.materials_ready ? (
            <PrimaryButton
              title="✨ Generate & Approve Tailored Résumé + Cover Letter"
              variant="secondary"
              loading={busyAction === "materials"}
              disabled={!!busyAction}
              onPress={() => void handlePrepareMaterials()}
            />
          ) : null}
        </View>
      ) : null}

      <PrimaryButton
        title="🌐 Open Live ATS Posting in Browser (Manual Handoff)"
        variant="secondary"
        onPress={() => void Linking.openURL(job.url)}
      />
    </Card>
  );
}

function JobHeader({ job }: { job: JobDetail }) {
  const theme = useTheme();
  const linked = linkedLabel(job);
  return (
    <View style={{ gap: 6 }}>
      <Text style={[styles.title, { color: theme.text }]}>{job.title}</Text>
      <Text style={[styles.meta, { color: theme.textMuted }]}>
        {job.company} · {job.location || "—"}
        {job.remote ? " · Remote" : ""}
      </Text>
      <View style={styles.badges}>
        <Badge label={humanize(job.stage)} color={stageColor(theme, job.stage)} />
        <Badge label={sourceLabel(job.source)} color={theme.info} />
        {linked ? <Badge label={linked} color={theme.textMuted} /> : null}
        <LivenessBadge status={livenessBadgeStatus(job)} />
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
      <View style={{ marginTop: 6 }}>
        <PrimaryButton
          title="👉 Answer Questions in Review Studio ›"
          onPress={() => {
            router.push({ pathname: "/review/[id]", params: { id: tasks[0].id } });
          }}
        />
      </View>
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

function QuotaCard() {
  const theme = useTheme();
  const router = useRouter();
  const plan = useSubscription((s) => s.plan);
  const remaining = useSubscription((s) => s.remainingToday());
  const isPro = plan === "pro";

  if (isPro) return null;

  return (
    <Card
      title="Daily Free Tier Quota"
      right={
        <Badge
          label={remaining > 0 ? `${remaining} remaining` : "Daily limit reached"}
          color={remaining > 0 ? theme.info : theme.warning}
        />
      }
    >
      <Muted>
        Free accounts can fill up to {MAX_DAILY_FREE_APPLICATIONS} applications per day.
        Upgrade to HunterX Pro for unlimited applications, full autonomous looping, and background scheduling.
      </Muted>
      {remaining === 0 && (
        <PrimaryButton
          title="Upgrade to HunterX Pro ›"
          onPress={() => router.push("/settings")}
        />
      )}
    </Card>
  );
}

function useDiscoveryActions(id: string, refresh: () => Promise<void>) {
  async function checkLiveness() {
    await api.checkLiveness(id);
    await refresh();
  }
  async function unlinkDuplicate() {
    const ok = await confirmAsync(
      "Not a duplicate?",
      "This posting will be scored again from scratch as its own role. Nothing is applied or submitted.",
      "Re-check"
    );
    if (!ok) return;
    await api.unlinkDuplicate(id);
    await refresh();
  }
  return { checkLiveness, unlinkDuplicate };
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
  const { checkLiveness, unlinkDuplicate } = useDiscoveryActions(String(id), refresh);

  if (loading && !job) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!job) return <ScreenContainer><ErrorView message={error ?? "Job not found."} onRetry={reload} /></ScreenContainer>;

  const openTasks = job.review_tasks.filter((task) => task.status === "open");

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}
        <QuotaCard />
        <JobHeader job={job} />
        <ExecutionCommandCard job={job} openTasks={openTasks} refresh={refresh} />
        <OpenTasksCard tasks={openTasks} />
        <FormStatusCard status={job.form_status} preview={preview} openTaskId={openTasks[0]?.id} />
        {previewError ? <Banner tone="danger" message={previewError} /> : null}
        <PrimaryButton title="🔍 Inspect Live ATS Form Schema (Read-Only)" variant="secondary" loading={checking} onPress={() => void checkForm()} />
        <ApplicationCard application={job.application} jobId={job.id} />
        <ScoreCard job={job} />
        <LinkedPostingsCard job={job} onUnlink={unlinkDuplicate} />
        <LivenessCard job={job} onCheck={checkLiveness} />
        <TimelineCard events={job.events} />
        <DescriptionCard description={job.description} />
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
  holdBox: { borderWidth: 1, borderRadius: 12, padding: 12, gap: 8, marginTop: 4 },
  holdTitle: { fontSize: 14, fontWeight: "800" },
  task: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 8, gap: 2 },
  event: { gap: 2 },
  eventStage: { fontSize: 13, fontWeight: "700" },
  description: { fontSize: 14, lineHeight: 20 },
});
