import { useRouter } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { ReviewTask } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Chip } from "@/components/Chip";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { notify } from "@/lib/confirm";
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
      style={({ pressed }) => [
        styles.row,
        {
          backgroundColor: theme.surface,
          borderColor: open ? theme.warning + "66" : theme.border,
          opacity: pressed ? 0.85 : 1,
        },
      ]}
    >
      <View style={styles.head}>
        <View style={{ flexDirection: "row", gap: 6, alignItems: "center" }}>
          <Badge label={humanize(task.reason_code)} color={open ? theme.warning : theme.textFaint} />
          {task.company ? (
            <Text style={[styles.companyBadge, { color: theme.textMuted }]}>
              {task.company}
            </Text>
          ) : null}
        </View>
        <Text style={[styles.when, { color: theme.textFaint }]}>{formatRelativeToNow(task.created_at)}</Text>
      </View>
      <Text style={[styles.title, { color: theme.text }]} numberOfLines={2}>
        {task.title}
      </Text>
      <View style={styles.footerRow}>
        <Text style={[styles.meta, { color: theme.primary }]}>Tap to answer questions & resolve ›</Text>
        {!open ? <Badge label={humanize(task.status)} color={theme.textMuted} /> : null}
      </View>
    </Pressable>
  );
}

export default function ReviewQueueScreen() {
  const theme = useTheme();
  const [status, setStatus] = useState<"open" | "closed">("open");
  const { data, loading, refreshing, error, refresh, reload } = useApiResource(() => api.reviewTasks(status), [status], 30000);
  const [revalidating, setRevalidating] = useState(false);

  async function handleRecheckAll() {
    setRevalidating(true);
    try {
      const res = await api.revalidateReviews();
      if (res.revalidated > 0) {
        notify("Vault Recheck", `Resolved ${res.revalidated} review tasks! Remaining open: ${res.remaining_open}`);
      } else {
        notify("Vault Recheck", "No open tasks could be auto-resolved with current vault answers.");
      }
      await refresh();
    } catch (err) {
      notify("Recheck Error", describeError(err));
    } finally {
      setRevalidating(false);
    }
  }

  const openCount = data && status === "open" ? data.length : 0;

  return (
    <ScreenContainer>
      <View style={styles.topBar}>
        <View style={styles.filters}>
          <Chip
            label={status === "open" && data ? `Open (${data.length})` : "Open"}
            selected={status === "open"}
            onPress={() => setStatus("open")}
          />
          <Chip
            label={status === "closed" && data ? `Closed (${data.length})` : "Closed"}
            selected={status === "closed"}
            onPress={() => setStatus("closed")}
          />
        </View>
        {status === "open" && openCount > 0 ? (
          <Pressable
            accessibilityRole="button"
            disabled={revalidating}
            onPress={() => void handleRecheckAll()}
            style={[styles.recheckBtn, { backgroundColor: theme.primary + "18", borderColor: theme.primary }]}
          >
            {revalidating ? (
              <ActivityIndicator size="small" color={theme.primary} />
            ) : (
              <Text style={[styles.recheckText, { color: theme.primary }]}>⚡ Recheck Vault</Text>
            )}
          </Pressable>
        ) : null}
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
          ListEmptyComponent={
            <EmptyView
              message={
                status === "open"
                  ? "All review questions are answered! Applications are ready to apply. 🎉"
                  : "No closed tasks yet."
              }
            />
          }
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
        />
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingTop: 12,
    gap: 8,
  },
  filters: { flexDirection: "row", gap: 8 },
  recheckBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
  },
  recheckText: { fontSize: 12, fontWeight: "700" },
  list: { padding: 16, gap: 10 },
  row: { padding: 14, borderRadius: 14, borderWidth: 1, gap: 8 },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  companyBadge: { fontSize: 12, fontWeight: "600" },
  when: { fontSize: 11 },
  title: { fontSize: 15, fontWeight: "700", lineHeight: 21 },
  footerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingTop: 4 },
  meta: { fontSize: 12, fontWeight: "600" },
});
