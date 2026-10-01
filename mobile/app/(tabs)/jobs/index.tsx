import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { api } from "@/api/client";
import type { Job } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Chip } from "@/components/Chip";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { matchScoreColor, stageColor, useTheme } from "@/theme";
import { formatRelativeToNow, formatScore, humanize } from "@/utils/format";

const STAGE_FILTERS: { label: string; stage?: string }[] = [
  { label: "All" },
  { label: "Shortlisted", stage: "shortlisted" },
  { label: "Ready", stage: "ready_to_apply" },
  { label: "Needs review", stage: "needs_review" },
  { label: "Validated", stage: "validated" },
  { label: "Review", stage: "review" },
  { label: "Rejected", stage: "rejected" },
];

function JobRow({ job }: { job: Job }) {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => router.push({ pathname: "/jobs/[id]", params: { id: job.id } })}
      style={({ pressed }) => [styles.row, { backgroundColor: theme.surface, borderColor: theme.border, opacity: pressed ? 0.85 : 1 }]}>
        <View style={[styles.score, { borderColor: matchScoreColor(theme, job.score) }]}>
          <Text style={[styles.scoreText, { color: matchScoreColor(theme, job.score) }]}>{formatScore(job.score)}</Text>
        </View>
        <View style={{ flex: 1, gap: 3 }}>
          <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>{job.title}</Text>
          <Text style={[styles.meta, { color: theme.textMuted }]} numberOfLines={1}>
            {job.company} · {job.location || "—"}
          </Text>
          <View style={styles.badges}>
            <Badge label={humanize(job.stage)} color={stageColor(theme, job.stage)} />
            {job.open_review_tasks ? <Badge label={`${job.open_review_tasks} review`} color={theme.warning} /> : null}
            <Text style={[styles.when, { color: theme.textFaint }]}>{formatRelativeToNow(job.discovered_at)}</Text>
          </View>
        </View>
    </Pressable>
  );
}

export default function JobsScreen() {
  const theme = useTheme();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [stage, setStage] = useState<string | undefined>(undefined);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 350);
    return () => clearTimeout(timer);
  }, [query]);

  const { data, loading, refreshing, error, refresh, reload } = useApiResource(
    () => api.jobs({ q: debounced || undefined, stage }),
    [debounced, stage]
  );

  return (
    <ScreenContainer>
      <View style={styles.filters}>
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Search title, company, location"
          placeholderTextColor={theme.textFaint}
          autoCorrect={false}
          style={[styles.search, { color: theme.text, backgroundColor: theme.surface, borderColor: theme.border }]}
          accessibilityLabel="Search jobs"
        />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          {STAGE_FILTERS.map((filter) => (
            <Chip key={filter.label} label={filter.label} selected={stage === filter.stage} onPress={() => setStage(filter.stage)} />
          ))}
        </ScrollView>
      </View>
      {loading && !data ? (
        <LoadingView />
      ) : !data ? (
        <ErrorView message={error ?? "No data."} onRetry={reload} />
      ) : (
        <FlatList
          data={data}
          keyExtractor={(job) => job.id}
          renderItem={({ item }) => <JobRow job={item} />}
          contentContainerStyle={styles.list}
          ListHeaderComponent={error ? <Banner tone="danger" message={error} /> : null}
          ListEmptyComponent={<EmptyView message={debounced || stage ? "No jobs match." : "No jobs yet. Discovery runs with each cycle."} />}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
        />
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  filters: { paddingHorizontal: 16, paddingTop: 12, gap: 10 },
  search: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 15 },
  chips: { gap: 8, paddingBottom: 4 },
  list: { padding: 16, gap: 10 },
  row: { flexDirection: "row", gap: 12, padding: 12, borderRadius: 14, borderWidth: 1, alignItems: "center" },
  score: { width: 46, height: 46, borderRadius: 23, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  scoreText: { fontSize: 16, fontWeight: "800" },
  title: { fontSize: 15, fontWeight: "700" },
  meta: { fontSize: 13 },
  badges: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 2 },
  when: { fontSize: 11 },
});
