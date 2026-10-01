import { useRouter } from "expo-router";
import { useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { Cycle } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { LockBanner } from "@/components/LockBanner";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { StatCard } from "@/components/StatCard";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { confirmAsync, notify } from "@/lib/confirm";
import { killSwitchRequest } from "@/safety";
import { cycleColor, useTheme } from "@/theme";
import { formatDateTime, formatRelativeToNow, humanize } from "@/utils/format";

async function loadDashboard() {
  const [status, summary, cycles] = await Promise.all([api.schedulerStatus(), api.summary(), api.cycles(5)]);
  return { status, summary, cycles };
}

function CycleRow({ cycle }: { cycle: Cycle }) {
  const theme = useTheme();
  return (
    <View style={[styles.cycle, { borderTopColor: theme.border }]}>
      <View style={styles.cycleHead}>
        <Badge label={humanize(cycle.status)} color={cycleColor(theme, cycle.status)} />
        <Text style={[styles.cycleTime, { color: theme.textMuted }]}>
          {humanize(cycle.trigger)} · {formatRelativeToNow(cycle.started_at)}
        </Text>
      </View>
      {cycle.reason ? <Text style={[styles.cycleReason, { color: theme.textMuted }]}>{cycle.reason}</Text> : null}
      {cycle.error ? <Text style={[styles.cycleReason, { color: theme.danger }]}>{cycle.error}</Text> : null}
    </View>
  );
}

export default function DashboardScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { data, loading, refreshing, error, refresh, reload } = useApiResource(loadDashboard, [], 15000);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(name: string, action: () => Promise<unknown>, done?: string) {
    setBusy(name);
    try {
      await action();
      if (done) notify("Done", done);
      await refresh();
    } catch (err) {
      notify("Couldn't do that", describeError(err));
    } finally {
      setBusy(null);
    }
  }

  async function toggleKillSwitch(engage: boolean) {
    const confirmed = engage || (await confirmAsync(
      "Disengage the kill switch?",
      "Scheduled cycles and dry-runs can start again (live submission stays locked).",
      "Disengage",
      true
    ));
    const body = killSwitchRequest(engage, confirmed);
    if (body) await act("kill", () => api.setKillSwitch(body));
  }

  async function runNow() {
    await act("run", async () => {
      const result = await api.runCycleNow();
      if (!result.started) throw new Error(result.reason ?? "Not started.");
    }, "Cycle started. Pull to refresh for results.");
  }

  if (loading && !data) return <ScreenContainer><LoadingView label="Contacting server…" /></ScreenContainer>;
  if (!data) {
    return (
      <ScreenContainer>
        <ErrorView message={error ?? "No data."} onRetry={reload} />
        <View style={{ padding: 16 }}>
          <PrimaryButton title="Open connection settings" variant="secondary" onPress={() => { router.push("/connection"); }} />
        </View>
      </ScreenContainer>
    );
  }

  const { status, summary, cycles } = data;
  const schedulerState = status.kill_switch
    ? { label: "Stopped (kill switch)", color: theme.danger }
    : status.paused
      ? { label: "Paused", color: theme.warning }
      : !status.continuous_run_enabled
        ? { label: "Off (CONTINUOUS_RUN_ENABLED=false)", color: theme.textMuted }
        : status.quiet_hours.active
          ? { label: "Quiet hours", color: theme.warning }
          : { label: "Running", color: theme.success };

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={`Showing the last data: ${error}`} /> : null}
        <LockBanner compact />

        <Card
          title="Kill switch"
          right={<Badge label={status.kill_switch ? "Engaged" : "Off"} color={status.kill_switch ? theme.danger : theme.success} />}
          style={status.kill_switch ? { borderColor: theme.danger } : undefined}
        >
          <Muted>
            {status.kill_switch
              ? "Everything is stopped: no cycles, no dry-runs, no approvals."
              : "Stops all cycles, dry-runs and approvals immediately."}
          </Muted>
          {status.kill_switch ? (
            <PrimaryButton title="Disengage…" variant="secondary" loading={busy === "kill"} onPress={() => void toggleKillSwitch(false)} />
          ) : (
            <PrimaryButton title="Engage kill switch" variant="danger" loading={busy === "kill"} onPress={() => void toggleKillSwitch(true)} />
          )}
        </Card>

        <Card title="Scheduler" right={<Badge label={schedulerState.label} color={schedulerState.color} />}>
          <Row label="Next run" value={status.next_run_at ? `${formatDateTime(status.next_run_at)} (${formatRelativeToNow(status.next_run_at)})` : "—"} />
          <Row label="Interval" value={`${status.interval_minutes} min`} />
          <Row
            label="Quiet hours"
            value={`${status.quiet_hours.start}–${status.quiet_hours.end} ${status.quiet_hours.timezone}`}
          />
          <Row label="Automation (dry-runs)" value={status.automation_enabled ? "On" : "Off"} valueColor={status.automation_enabled ? theme.success : theme.textMuted} />
          <Row label="Cycle in progress" value={status.cycle_in_progress ? "Yes" : "No"} />
          <View style={styles.buttons}>
            <View style={{ flex: 1 }}>
              {status.paused ? (
                <PrimaryButton title="Resume" variant="secondary" loading={busy === "pause"} onPress={() => void act("pause", api.resumeScheduler)} />
              ) : (
                <PrimaryButton title="Pause" variant="secondary" loading={busy === "pause"} onPress={() => void act("pause", api.pauseScheduler)} />
              )}
            </View>
            <View style={{ flex: 1 }}>
              <PrimaryButton
                title="Run now"
                loading={busy === "run"}
                disabled={status.kill_switch || status.cycle_in_progress}
                onPress={() => void runNow()}
              />
            </View>
          </View>
        </Card>

        <Text style={[styles.section, { color: theme.textMuted }]}>TODAY</Text>
        <View style={styles.grid}>
          <StatCard label="Dry-runs" value={`${status.today.dry_runs} / ${status.limits.max_dry_runs_per_day}`} hint="daily cap" />
          <StatCard label="Submissions" value={`${status.today.submissions} / ${status.limits.max_applications_per_day}`} hint="locked: always 0" />
          <StatCard label="Open reviews" value={String(summary.review.open)} hint="needs you" />
          <StatCard label="New jobs" value={String(summary.jobs.discovered_today)} hint={`${summary.jobs.total} total`} />
        </View>

        <Card title="Recent cycles">
          {cycles.length === 0 ? <Muted>No cycles yet.</Muted> : cycles.map((cycle) => <CycleRow key={cycle.id} cycle={cycle} />)}
        </Card>
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  buttons: { flexDirection: "row", gap: 10, marginTop: 4 },
  section: { fontSize: 12, fontWeight: "800", letterSpacing: 0.6, marginTop: 4 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  cycle: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 8, gap: 4 },
  cycleHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  cycleTime: { fontSize: 12 },
  cycleReason: { fontSize: 12, lineHeight: 17 },
});
