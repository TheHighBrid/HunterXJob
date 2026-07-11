import { useCallback } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api } from "@/api/client";
import { DemoDataBanner } from "@/components/DemoDataBanner";
import { ScreenContainer } from "@/components/ScreenContainer";
import { StatCard } from "@/components/StatCard";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { mockHealth, mockReports } from "@/mock/data";
import { useTheme } from "@/theme";
import { formatRelativeToNow } from "@/utils/format";

const getHealth = () => api.getHealth();
const getLatestReport = () => api.getLatestReport();
const demoLatestReport = mockReports[0];

export default function DashboardScreen() {
  const theme = useTheme();

  const health = useApiResource(getHealth, mockHealth);
  const report = useApiResource(getLatestReport, demoLatestReport);

  const refreshing = health.refreshing || report.refreshing;
  const loading = health.loading || report.loading;

  const onRefresh = useCallback(() => {
    health.refresh();
    report.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading dashboard…" />
      </ScreenContainer>
    );
  }

  // Both calls failed hard (non-connectivity error, e.g. bad API key) and
  // neither has data to show at all.
  if (!health.data && !report.data) {
    return (
      <ScreenContainer>
        <ErrorView
          message={health.error ?? report.error ?? "Unable to load the dashboard."}
          onRetry={onRefresh}
        />
      </ScreenContainer>
    );
  }

  const summary = report.data?.summary;
  const isDemo = health.isDemo || report.isDemo;
  const hardError = (!health.isDemo && health.error) || (!report.isDemo && report.error);

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.primary} />}
      >
        {isDemo ? <DemoDataBanner reason={health.error ?? report.error ?? undefined} /> : null}
        {!isDemo && hardError ? (
          <View style={[styles.errorBanner, { backgroundColor: theme.danger + "22", borderColor: theme.danger }]}>
            <Text style={{ color: theme.text, fontSize: 13 }}>{hardError}</Text>
          </View>
        ) : null}

        <Text style={[styles.sectionTitle, { color: theme.text }]}>Today</Text>
        <View style={styles.statGrid}>
          <StatCard label="Jobs discovered today" value={String(summary?.jobs_discovered_today ?? 0)} />
          <StatCard label="Applications submitted today" value={String(summary?.applications_submitted_today ?? 0)} />
        </View>

        <Text style={[styles.sectionTitle, { color: theme.text }]}>This week</Text>
        <View style={styles.statGrid}>
          <StatCard label="Submitted this week" value={String(summary?.applications_submitted_this_week ?? 0)} />
          <StatCard label="Pending review" value={String(summary?.pending_review_count ?? 0)} />
        </View>

        <Text style={[styles.sectionTitle, { color: theme.text }]}>Automation</Text>
        <View style={styles.statGrid}>
          <StatCard
            label="Next scheduled run"
            value={
              health.data?.next_scheduled_run
                ? formatRelativeToNow(health.data.next_scheduled_run)
                : health.data?.scheduler_running
                  ? "Pending"
                  : "Not running"
            }
          />
          <StatCard
            label="Backend status"
            value={health.data?.status === "ok" ? "Online" : (health.data?.status ?? "Unknown")}
            hint={health.data?.llm_provider ? `LLM: ${health.data.llm_provider}` : undefined}
          />
        </View>

        {summary?.highlights?.length ? (
          <View style={styles.highlightsSection}>
            <Text style={[styles.sectionTitle, { color: theme.text }]}>Latest report highlights</Text>
            {summary.highlights.map((h, i) => (
              <View key={i} style={[styles.highlightRow, { borderColor: theme.border }]}>
                <Text style={[styles.highlightText, { color: theme.textMuted }]}>{"• " + h}</Text>
              </View>
            ))}
          </View>
        ) : null}
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 16,
    paddingBottom: 40,
    gap: 4,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "700",
    marginTop: 18,
    marginBottom: 8,
  },
  statGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  errorBanner: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 4,
  },
  highlightsSection: {
    marginTop: 8,
  },
  highlightRow: {
    paddingVertical: 6,
  },
  highlightText: {
    fontSize: 13,
    lineHeight: 18,
  },
});
