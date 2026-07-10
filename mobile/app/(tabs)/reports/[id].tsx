import { useLocalSearchParams, useNavigation } from "expo-router";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError, isConnectivityError } from "@/api/client";
import { ScreenContainer } from "@/components/ScreenContainer";
import { StatCard } from "@/components/StatCard";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { mockReports } from "@/mock/data";
import { useDataCacheStore } from "@/store/dataCache";
import { useTheme } from "@/theme";
import type { Report } from "@/types";
import { formatDateTime } from "@/utils/format";

export default function ReportDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const reportId = id;
  const theme = useTheme();
  const navigation = useNavigation();
  const cachedReports = useDataCacheStore((s) => s.reports);

  const cached = useMemo(() => cachedReports.find((r) => r.id === reportId), [cachedReports, reportId]);

  const [report, setReport] = useState<Report | null | undefined>(cached);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);

  useLayoutEffect(() => {
    navigation.setOptions({ title: report?.period ?? "Report" });
  }, [navigation, report?.period]);

  useEffect(() => {
    if (cached) {
      setReport(cached);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getReports()
      .then((reports) => {
        if (cancelled) return;
        setReport(reports.find((r) => r.id === reportId) ?? null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        if (isConnectivityError(err)) {
          setReport(mockReports.find((r) => r.id === reportId) ?? null);
          setError(`${describeError(err)} (showing demo data)`);
        } else {
          setError(describeError(err));
        }
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cached, reportId]);

  if (loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading report…" />
      </ScreenContainer>
    );
  }

  if (!report) {
    return (
      <ScreenContainer>
        {error ? <ErrorView message={error} /> : <EmptyView message="Report not found." />}
      </ScreenContainer>
    );
  }

  const { summary } = report;

  return (
    <ScreenContainer>
      <ScrollView contentContainerStyle={styles.content}>
        {error ? (
          <View style={[styles.noticeBanner, { backgroundColor: theme.warning + "22", borderColor: theme.warning }]}>
            <Text style={{ color: theme.text, fontSize: 12 }}>{error}</Text>
          </View>
        ) : null}

        <Text style={[styles.title, { color: theme.text }]}>{report.period}</Text>
        <Text style={[styles.subtitle, { color: theme.textFaint }]}>Generated {formatDateTime(report.generated_at)}</Text>

        <View style={styles.statGrid}>
          <StatCard label="Jobs discovered" value={String(summary.jobs_discovered_today)} />
          <StatCard label="Submitted today" value={String(summary.applications_submitted_today)} />
          <StatCard label="Submitted this week" value={String(summary.applications_submitted_this_week)} />
          <StatCard label="Pending review" value={String(summary.pending_review_count)} />
          <StatCard label="Blocked" value={String(summary.applications_blocked)} />
        </View>

        {summary.highlights.length ? (
          <>
            <Text style={[styles.sectionTitle, { color: theme.text }]}>Highlights</Text>
            {summary.highlights.map((h, i) => (
              <Text key={i} style={[styles.listItem, { color: theme.textMuted }]}>
                {"• " + h}
              </Text>
            ))}
          </>
        ) : null}

        {summary.errors.length ? (
          <>
            <Text style={[styles.sectionTitle, { color: theme.danger }]}>Errors</Text>
            {summary.errors.map((e, i) => (
              <Text key={i} style={[styles.listItem, { color: theme.danger }]}>
                {"• " + e}
              </Text>
            ))}
          </>
        ) : null}
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 16,
    paddingBottom: 40,
  },
  noticeBanner: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 8,
  },
  title: {
    fontSize: 22,
    fontWeight: "800",
  },
  subtitle: {
    fontSize: 13,
    marginBottom: 16,
  },
  statGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginBottom: 8,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "700",
    marginTop: 18,
    marginBottom: 8,
  },
  listItem: {
    fontSize: 13,
    lineHeight: 20,
  },
});
