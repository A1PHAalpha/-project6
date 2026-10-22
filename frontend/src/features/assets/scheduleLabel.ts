import type { CollectionSchedule } from "../../shared/types";

export function scheduleLabel(schedule: CollectionSchedule) {
  if (!schedule.scheduler_available) {
    return {
      active: false,
      title: "自动采集调度未启用",
      detail: "计划配置已保存，当前需要手动采集"
    };
  }
  return {
    active: schedule.enabled,
    title: schedule.enabled ? "自动采集已开启" : "自动采集已关闭",
    detail: `每 ${schedule.frequency_minutes} 分钟`
  };
}
