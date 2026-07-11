import { useRouter } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { api } from "@/api/client";
import { Badge } from "@/components/Badge";
import { DemoDataBanner } from "@/components/DemoDataBanner";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { mockApplications } from "@/mock/data";
import { useDataCacheStore } from "@/store/dataCache";
import { statusColor, useTheme } from "@/theme";
import { APPLICATION_STATUSES, type ApplicationRecord, type ApplicationStatus } from "@/types";
import { formatDate, titleCase } from "@/utils/format";

const getApplications = () => api.getApplications();

type FilterValue = "all" | ApplicationStatus;

export default function ApplicationsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const setApplications = useDataCacheStore((s) => s.setApplications);
  const [filter, setFilter] = useState<FilterValue>("all");

  const applications = useApiResource(getApplications, mockApplications);

  useEffect(() => {
    if (applications.data) setApplications(applications.data, applications.isDemo);
  }, [applications.data, applications.isDemo, setApplications]);

  const sections = useMemo(() => {
    if (!applications.data) return [];
    const items = applications.data;
    if (filter !== "all") {
      const filtered = items.filter((a) => a.status === filter);
      return filtered.length ? [{ title: titleCase(filter), data: filtered }] : [];
    }
    const byStatus = new Map<ApplicationStatus, ApplicationRecord[]>();
    for (const status of APPLICATION_STATUSES) byStatus.set(status, []);
    for (const app of items) {
      if (!byStatus.has(app.status)) byStatus.set(app.status, []);
      byStatus.get(app.status)!.push(app);
    }
    return APPLICATION_STATUSES.filter((s) => (byStatus.get(s)?.length ?? 0) > 0).map((status) => ({
      title: titleCase(status),
      data: byStatus.get(status)!,
    }));
  }, [applications.data, filter]);

  if (applications.loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading applications…" />
      </ScreenContainer>
    );
  }

  if (!applications.data) {
    return (
      <ScreenContainer>
        <ErrorView message={applications.error ?? "Unable to load applications."} onRetry={applications.reload} />
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filterRow}
        style={{ flexGrow: 0 }}
      >
        <FilterChip label="All" active={filter === "all"} onPress={() => setFilter("all")} />
        {APPLICATION_STATUSES.map((status) => (
          <FilterChip
            key={status}
            label={titleCase(status)}
            active={filter === status}
            color={statusColor(theme, status)}
            onPress={() => setFilter(status)}
          />
        ))}
      </ScrollView>

      <SectionList
        sections={sections}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.listContent}
        refreshControl={
          <RefreshControl refreshing={applications.refreshing} onRefresh={applications.refresh} tintColor={theme.primary} />
        }
        ListHeaderComponent={applications.isDemo ? <DemoDataBanner reason={applications.error ?? undefined} /> : null}
        ListEmptyComponent={<EmptyView message="No tracked applications yet." />}
        renderSectionHeader={({ section }) => (
          <Text style={[styles.sectionHeader, { color: theme.textFaint, backgroundColor: theme.background }]}>
            {section.title.toUpperCase()} ({section.data.length})
          </Text>
        )}
        renderItem={({ item }) => (
          <ApplicationRow application={item} onPress={() => router.push(`/applications/${item.id}`)} />
        )}
      />
    </ScreenContainer>
  );
}

function FilterChip({
  label,
  active,
  onPress,
  color,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  color?: string;
}) {
  const theme = useTheme();
  const chipColor = color ?? theme.primary;
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.chip,
        {
          backgroundColor: active ? chipColor + "26" : theme.surface,
          borderColor: active ? chipColor : theme.border,
        },
      ]}
    >
      <Text style={{ color: active ? chipColor : theme.textMuted, fontSize: 12, fontWeight: "700" }}>{label}</Text>
    </Pressable>
  );
}

function ApplicationRow({ application, onPress }: { application: ApplicationRecord; onPress: () => void }) {
  const theme = useTheme();
  const color = statusColor(theme, application.status);
  const job = application.job;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        { backgroundColor: theme.surface, borderColor: theme.border, opacity: pressed ? 0.8 : 1 },
      ]}
    >
      <View style={styles.rowTop}>
        <Text style={[styles.title, { color: theme.text }]} numberOfLines={1}>
          {job?.title ?? `Job #${application.job_posting_id}`}
        </Text>
        <Badge label={titleCase(application.status)} color={color} />
      </View>
      <Text style={[styles.company, { color: theme.textMuted }]} numberOfLines={1}>
        {job?.company ?? "Unknown company"}
      </Text>
      <Text style={[styles.meta, { color: theme.textFaint }]}>
        {application.channel} · {application.submitted_at ? `submitted ${formatDate(application.submitted_at)}` : "not submitted"}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  filterRow: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    gap: 8,
  },
  chip: {
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginRight: 8,
  },
  listContent: {
    padding: 16,
    paddingTop: 0,
    flexGrow: 1,
  },
  sectionHeader: {
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 0.5,
    paddingVertical: 8,
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
    fontSize: 15,
    fontWeight: "700",
    flex: 1,
  },
  company: {
    fontSize: 13,
    fontWeight: "600",
  },
  meta: {
    fontSize: 12,
  },
});
