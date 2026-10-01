import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api } from "@/api/client";
import type { HistoryDay } from "@/api/types";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { LockBanner } from "@/components/LockBanner";
import { ScreenContainer } from "@/components/ScreenContainer";
import { StatCard } from "@/components/StatCard";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { cycleColor, stageColor, useTheme, type Theme } from "@/theme";
import { formatDateTime, humanize } from "@/utils/format";

type Palette = "stage" | "cycle" | "warning";

function barColor(theme: Theme, palette: Palette, key: string): string {
  if (palette === "stage") return stageColor(theme, key);
  if (palette === "cycle") return cycleColor(theme, key);
  return theme.warning;
}

function byCount(record: Record<string, number>): [string, number][] {
  return Object.entries(record).sort((a, b) => b[1] - a[1]);
}

function Bars({ entries, palette }: { entries: [string, number][]; palette: Palette }) {
  const theme = useTheme();
  const max = Math.max(1, ...entries.map(([, n]) => n));
  if (entries.length === 0) return <Muted>Nothing yet.</Muted>;
  return (
    <View style={{ gap: 8 }}>
      {entries.map(([key, count]) => (
        <View key={key} style={styles.barRow}>
          <Text style={[styles.barLabel, { color: theme.textMuted }]} numberOfLines={1}>{humanize(key)}</Text>
          <View style={[styles.barTrack, { backgroundColor: theme.surfaceAlt }]}>
            <View style={[styles.barFill, { width: `${(count / max) * 100}%`, backgroundColor: barColor(theme, palette, key) }]} />
          </View>
          <Text style={[styles.barCount, { color: theme.text }]}>{count}</Text>
        </View>
      ))}
    </View>
  );
}

function History({ days }: { days: HistoryDay[] }) {
  const theme = useTheme();
  const max = Math.max(1, ...days.map((d) => Math.max(d.discovered, d.dry_runs)));
  return (
    <View style={styles.history}>
      {days.map((day) => (
        <View key={day.date} style={styles.historyDay}>
          <View style={styles.historyBars}>
            <View style={[styles.historyBar, { height: `${(day.discovered / max) * 100}%`, backgroundColor: theme.info }]} />
            <View style={[styles.historyBar, { height: `${(day.dry_runs / max) * 100}%`, backgroundColor: theme.success }]} />
          </View>
          <Text style={[styles.historyLabel, { color: theme.textFaint }]}>{day.date.slice(5)}</Text>
        </View>
      ))}
    </View>
  );
}

export default function ReportsScreen() {
  const theme = useTheme();
  const { data, loading, refreshing, error, refresh, reload } = useApiResource(() => api.summary(), [], 60000);

  if (loading && !data) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!data) return <ScreenContainer><ErrorView message={error ?? "No data."} onRetry={reload} /></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}
        <View style={styles.grid}>
          <StatCard label="Jobs discovered" value={String(data.jobs.total)} hint={`${data.jobs.discovered_today} today`} />
          <StatCard label="Scored" value={String(data.jobs.scored)} hint={`${data.jobs.shortlisted} shortlisted`} />
          <StatCard label="Dry-runs passed" value={String(data.dry_runs.completed_total)} hint={`${data.dry_runs.completed_today} today`} />
          <StatCard label="Review tasks" value={String(data.review.open)} hint={`${data.review.closed_total} closed`} />
        </View>

        <Card title="Last 7 days">
          <History days={data.history} />
          <View style={styles.legend}>
            <Text style={{ color: theme.info, fontSize: 12, fontWeight: "700" }}>■ discovered</Text>
            <Text style={{ color: theme.success, fontSize: 12, fontWeight: "700" }}>■ dry-runs</Text>
            <Text style={{ color: theme.textMuted, fontSize: 12 }}>
              {data.history.reduce((n, d) => n + d.cycles, 0)} cycles ran
            </Text>
          </View>
        </Card>

        <Card title="Jobs by stage">
          <Bars entries={byCount(data.jobs.by_stage)} palette="stage" />
        </Card>

        <Card title="Open review reasons">
          <Bars entries={byCount(data.review.open_by_reason)} palette="warning" />
        </Card>

        <Card title="Cycles (24h)">
          <Bars entries={byCount(data.cycles.last_24h)} palette="cycle" />
          {data.cycles.last ? (
            <Row label="Last cycle" value={`${humanize(data.cycles.last.status)} · ${formatDateTime(data.cycles.last.started_at)}`} />
          ) : null}
        </Card>

        <Card title="Submissions">
          <Row label="Today" value={`${data.submissions.today} / ${data.submissions.daily_cap}`} />
          <Row label="All time" value={String(data.submissions.total)} />
          <LockBanner compact />
        </Card>
        <Muted>Updated {formatDateTime(data.generated_at)} · days in {data.timezone}</Muted>
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  barRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  barLabel: { width: 120, fontSize: 12 },
  barTrack: { flex: 1, height: 10, borderRadius: 5, overflow: "hidden" },
  barFill: { height: 10, borderRadius: 5 },
  barCount: { width: 32, textAlign: "right", fontSize: 13, fontWeight: "700" },
  history: { flexDirection: "row", justifyContent: "space-between", height: 110, alignItems: "flex-end" },
  historyDay: { alignItems: "center", flex: 1, gap: 4, height: "100%" },
  historyBars: { flex: 1, flexDirection: "row", alignItems: "flex-end", gap: 3 },
  historyBar: { width: 9, borderRadius: 3, minHeight: 2 },
  historyLabel: { fontSize: 10 },
  legend: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
});
