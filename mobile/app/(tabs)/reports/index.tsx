import { useRouter } from "expo-router";
import { useEffect } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { api } from "@/api/client";
import { DemoDataBanner } from "@/components/DemoDataBanner";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { mockReports } from "@/mock/data";
import { useDataCacheStore } from "@/store/dataCache";
import { useTheme } from "@/theme";
import type { Report } from "@/types";
import { formatDateTime } from "@/utils/format";

const getReports = () => api.getReports();

export default function ReportsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const setReports = useDataCacheStore((s) => s.setReports);

  const reports = useApiResource(getReports, mockReports);

  useEffect(() => {
    if (reports.data) setReports(reports.data, reports.isDemo);
  }, [reports.data, reports.isDemo, setReports]);

  if (reports.loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading reports…" />
      </ScreenContainer>
    );
  }

  if (!reports.data) {
    return (
      <ScreenContainer>
        <ErrorView message={reports.error ?? "Unable to load reports."} onRetry={reports.reload} />
      </ScreenContainer>
    );
  }

  const sorted = [...reports.data].sort(
    (a, b) => new Date(b.generated_at).getTime() - new Date(a.generated_at).getTime()
  );

  return (
    <ScreenContainer>
      <FlatList
        data={sorted}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.listContent}
        refreshControl={<RefreshControl refreshing={reports.refreshing} onRefresh={reports.refresh} tintColor={theme.primary} />}
        ListHeaderComponent={reports.isDemo ? <DemoDataBanner reason={reports.error ?? undefined} /> : null}
        ListEmptyComponent={<EmptyView message="No reports generated yet." />}
        renderItem={({ item }) => <ReportRow report={item} onPress={() => router.push(`/reports/${item.id}`)} />}
      />
    </ScreenContainer>
  );
}

function ReportRow({ report, onPress }: { report: Report; onPress: () => void }) {
  const theme = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        { backgroundColor: theme.surface, borderColor: theme.border, opacity: pressed ? 0.8 : 1 },
      ]}
    >
      <View style={styles.rowTop}>
        <Text style={[styles.period, { color: theme.text }]}>{report.period}</Text>
        <Text style={[styles.date, { color: theme.textFaint }]}>{formatDateTime(report.generated_at)}</Text>
      </View>
      <Text style={[styles.summary, { color: theme.textMuted }]} numberOfLines={2}>
        {report.summary.applications_submitted_today} submitted today · {report.summary.pending_review_count} pending review
        {report.summary.applications_blocked ? ` · ${report.summary.applications_blocked} blocked` : ""}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  listContent: {
    padding: 16,
    flexGrow: 1,
  },
  row: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    gap: 4,
  },
  rowTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  period: {
    fontSize: 15,
    fontWeight: "700",
  },
  date: {
    fontSize: 12,
  },
  summary: {
    fontSize: 13,
    lineHeight: 18,
  },
});
