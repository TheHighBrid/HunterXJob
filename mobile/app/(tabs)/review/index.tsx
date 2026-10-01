import { useRouter } from "expo-router";
import { useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { api } from "@/api/client";
import type { ReviewTask } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Chip } from "@/components/Chip";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { useTheme } from "@/theme";
import { formatRelativeToNow, humanize } from "@/utils/format";

function TaskRow({ task }: { task: ReviewTask }) {
  const theme = useTheme();
  const open = task.status === "open";
  const router = useRouter();
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => {
        router.push({ pathname: "/review/[id]", params: { id: task.id } });
      }}
      style={({ pressed }) => [styles.row, { backgroundColor: theme.surface, borderColor: theme.border, opacity: pressed ? 0.85 : 1 }]}>
        <View style={styles.head}>
          <Badge label={humanize(task.reason_code)} color={open ? theme.warning : theme.textFaint} />
          <Text style={[styles.when, { color: theme.textFaint }]}>{formatRelativeToNow(task.created_at)}</Text>
        </View>
        <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>{task.title}</Text>
        {task.company ? <Text style={[styles.meta, { color: theme.textMuted }]}>{task.company}</Text> : null}
        {!open ? <Text style={[styles.meta, { color: theme.textMuted }]}>{humanize(task.status)}</Text> : null}
    </Pressable>
  );
}

export default function ReviewQueueScreen() {
  const theme = useTheme();
  const [status, setStatus] = useState<"open" | "closed">("open");
  const { data, loading, refreshing, error, refresh, reload } = useApiResource(() => api.reviewTasks(status), [status], 30000);

  return (
    <ScreenContainer>
      <View style={styles.filters}>
        <Chip label="Open" selected={status === "open"} onPress={() => {
          setStatus("open");
        }} />
        <Chip label="Closed" selected={status === "closed"} onPress={() => {
          setStatus("closed");
        }} />
      </View>
      {loading && !data ? (
        <LoadingView />
      ) : !data ? (
        <ErrorView message={error ?? "No data."} onRetry={reload} />
      ) : (
        <FlatList
          data={data}
          keyExtractor={(task) => task.id}
          renderItem={({ item }) => <TaskRow task={item} />}
          contentContainerStyle={styles.list}
          ListHeaderComponent={error ? <Banner tone="danger" message={error} /> : null}
          ListEmptyComponent={<EmptyView message={status === "open" ? "Nothing needs you right now. 🎉" : "No closed tasks yet."} />}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
        />
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  filters: { flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingTop: 12 },
  list: { padding: 16, gap: 10 },
  row: { padding: 12, borderRadius: 14, borderWidth: 1, gap: 6 },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  when: { fontSize: 11 },
  title: { fontSize: 15, fontWeight: "700" },
  meta: { fontSize: 13 },
});
