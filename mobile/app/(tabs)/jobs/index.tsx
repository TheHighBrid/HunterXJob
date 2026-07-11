import { useRouter } from "expo-router";
import { useEffect } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { api } from "@/api/client";
import { Badge } from "@/components/Badge";
import { DemoDataBanner } from "@/components/DemoDataBanner";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { mockJobs } from "@/mock/data";
import { useDataCacheStore } from "@/store/dataCache";
import { matchScoreColor, useTheme } from "@/theme";
import type { JobPosting } from "@/types";
import { formatRelativeToNow } from "@/utils/format";

const getJobs = () => api.getJobs();

export default function JobFeedScreen() {
  const theme = useTheme();
  const router = useRouter();
  const setJobs = useDataCacheStore((s) => s.setJobs);

  const jobs = useApiResource(getJobs, mockJobs);

  useEffect(() => {
    if (jobs.data) setJobs(jobs.data, jobs.isDemo);
  }, [jobs.data, jobs.isDemo, setJobs]);

  if (jobs.loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading job feed…" />
      </ScreenContainer>
    );
  }

  if (!jobs.data) {
    return (
      <ScreenContainer>
        <ErrorView message={jobs.error ?? "Unable to load jobs."} onRetry={jobs.reload} />
      </ScreenContainer>
    );
  }

  const sorted = [...jobs.data].sort((a, b) => (b.match_score ?? -1) - (a.match_score ?? -1));

  return (
    <ScreenContainer>
      <FlatList
        data={sorted}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.listContent}
        refreshControl={<RefreshControl refreshing={jobs.refreshing} onRefresh={jobs.refresh} tintColor={theme.primary} />}
        ListHeaderComponent={
          jobs.isDemo ? <DemoDataBanner reason={jobs.error ?? undefined} /> : null
        }
        ListEmptyComponent={<EmptyView message="No jobs discovered yet." />}
        renderItem={({ item }) => <JobRow job={item} onPress={() => router.push(`/jobs/${item.id}`)} />}
      />
    </ScreenContainer>
  );
}

function JobRow({ job, onPress }: { job: JobPosting; onPress: () => void }) {
  const theme = useTheme();
  const scoreColor = matchScoreColor(theme, job.match_score);

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        { backgroundColor: theme.surface, borderColor: theme.border, opacity: pressed ? 0.8 : 1 },
      ]}
    >
      <View style={styles.rowTop}>
        <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>
          {job.title}
        </Text>
        {job.match_score !== null ? (
          <Badge label={`${job.match_score}`} color={scoreColor} />
        ) : (
          <Badge label="Unscored" color={theme.textFaint} />
        )}
      </View>
      <Text style={[styles.company, { color: theme.textMuted }]}>{job.company}</Text>
      <View style={styles.metaRow}>
        <Text style={[styles.meta, { color: theme.textFaint }]}>
          {job.location ?? "Location unknown"}
          {job.remote ? " · Remote" : ""}
        </Text>
      </View>
      <View style={styles.metaRow}>
        <Text style={[styles.meta, { color: theme.textFaint }]}>
          {job.source} · discovered {formatRelativeToNow(job.discovered_at)}
        </Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  listContent: {
    padding: 16,
    gap: 10,
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
    alignItems: "flex-start",
    gap: 8,
  },
  title: {
    fontSize: 16,
    fontWeight: "700",
    flex: 1,
  },
  company: {
    fontSize: 14,
    fontWeight: "600",
  },
  metaRow: {
    flexDirection: "row",
  },
  meta: {
    fontSize: 12,
  },
});
