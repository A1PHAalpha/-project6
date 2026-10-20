import { StatusBadge } from "../../shared/components/ui";
import type { Device, Reachability, Tone } from "../../shared/types";

function reachabilityTone(value: Reachability): Tone {
  if (value === "reachable") return "success";
  if (value === "unreachable") return "danger";
  return "neutral";
}

export function AssetRow({
  device,
  selecting,
  selected,
  onSelect,
  onOpen
}: {
  device: Device;
  selecting: boolean;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onOpen: (anchor: DOMRect) => void;
}) {
  return (
    <tr
      className={selecting ? "selecting-row" : "clickable-row"}
      onClick={(event) => {
        if (!selecting) onOpen(event.currentTarget.getBoundingClientRect());
      }}
    >
      {selecting ? (
        <td className="selection-cell" onClick={(event) => event.stopPropagation()}>
          <input type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />
        </td>
      ) : null}
      <td>
        <StatusBadge tone={reachabilityTone(device.reachability)}>
          {device.reachability === "reachable"
            ? "可达"
            : device.reachability === "unreachable"
              ? "不可达"
              : "未采集"}
        </StatusBadge>
      </td>
      <td>
        <span className="cell-primary">{device.name}</span>
        <span className="cell-secondary">{device.role}</span>
      </td>
      <td>{device.site}</td>
      <td className="mono">{device.managementIp}</td>
      <td>{device.platform}</td>
      <td>{device.collectedAt ? new Date(device.collectedAt).toLocaleTimeString("zh-CN") : "—"}</td>
      <td>
        <span className="fact-pills">
          {device.facts.slice(0, 2).map((fact) => (
            <span className={`tag tone-${fact.tone}`} key={fact.id}>
              {fact.label} {fact.value}
            </span>
          ))}
          {!device.facts.length ? <span className="tag plain">未采集</span> : null}
        </span>
      </td>
      <td>
        <button
          className="button button-quiet button-xs"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onOpen(event.currentTarget.getBoundingClientRect());
          }}
        >
          详情
        </button>
      </td>
    </tr>
  );
}
