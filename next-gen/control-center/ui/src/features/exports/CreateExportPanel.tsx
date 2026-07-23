import { Play, RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ArtifactMetadata, CreateExportRequest, RegistryEntry } from "../../types/index";
import { HealthChecklist, type HealthItem } from "../../components/status";
import {
  defaultOutputPath,
  formatNumber,
  labelize,
  profileTables,
  type ExportCreateDraft,
  type ExportProfileTable,
  uniqueCities,
} from "./exportUtils";

export interface CreateExportPanelProps {
  profiles: RegistryEntry[];
  exports?: ArtifactMetadata[];
  preview?: Record<string, unknown> | null;
  busy?: boolean;
  message?: string;
  onPreview?: (draft: ExportCreateDraft) => void | Promise<void>;
  onCreateExport?: (request: CreateExportRequest, draft: ExportCreateDraft) => void | Promise<void>;
}

export function CreateExportPanel({
  profiles,
  exports = [],
  preview,
  busy = false,
  message,
  onPreview,
  onCreateExport,
}: CreateExportPanelProps) {
  const [profileId, setProfileId] = useState(profiles[0]?.id ?? "");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [outputPath, setOutputPath] = useState("");
  const [selectedCities, setSelectedCities] = useState<string[]>([]);
  const [tables, setTables] = useState<ExportProfileTable[]>([]);
  const activeProfile = profiles.find((profile) => profile.id === profileId) ?? profiles[0];
  const cities = useMemo(() => uniqueCities(exports), [exports]);

  useEffect(() => {
    if (!profileId && profiles[0]?.id) setProfileId(profiles[0].id);
  }, [profileId, profiles]);

  useEffect(() => {
    const nextTables = profileTables(activeProfile);
    setTables(nextTables.map((table) => ({ ...table, include: table.include ?? true })));
  }, [activeProfile]);

  useEffect(() => {
    if (!outputPath && profileId && start && end) {
      setOutputPath(defaultOutputPath(profileId, start, end));
    }
  }, [end, outputPath, profileId, start]);

  const draft: ExportCreateDraft = {
    profile_id: profileId,
    start,
    end,
    output_path: outputPath,
    cities: selectedCities,
    tables,
  };
  const validation = createValidationItems(draft);

  return (
    <aside className="panel create-export-panel">
      <header className="panel-title-row">
        <div>
          <h3>Create Export</h3>
          <p>Profile-backed local dataset generation</p>
        </div>
        <button
          className="secondary"
          disabled={!profileId || busy}
          onClick={() => void onPreview?.(draft)}
          type="button"
        >
          <RefreshCcw size={15} />
          Preview
        </button>
      </header>

      <label className="form-field">
        <span>Profile</span>
        <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.label ?? profile.id}
            </option>
          ))}
        </select>
      </label>

      <div className="field-grid">
        <label className="form-field">
          <span>Start</span>
          <input value={start} onChange={(event) => setStart(event.target.value)} placeholder="YYYY-MM-DD" />
        </label>
        <label className="form-field">
          <span>End</span>
          <input value={end} onChange={(event) => setEnd(event.target.value)} placeholder="YYYY-MM-DD" />
        </label>
      </div>

      <label className="form-field">
        <span>Output Path</span>
        <input
          value={outputPath}
          onChange={(event) => setOutputPath(event.target.value)}
          placeholder="data/export_name"
        />
      </label>

      <section className="subpanel">
        <h4>Cities</h4>
        <div className="chip-row">
          {cities.length ? (
            cities.map((city) => {
              const checked = selectedCities.includes(city);
              return (
                <button
                  className={checked ? "chip active" : "chip"}
                  key={city}
                  onClick={() => setSelectedCities(toggleValue(selectedCities, city))}
                  type="button"
                >
                  {city}
                </button>
              );
            })
          ) : (
            <p className="muted">City list will populate from available exports or preview metadata.</p>
          )}
        </div>
      </section>

      <section className="subpanel">
        <h4>Tables</h4>
        <div className="table-choice-list">
          {tables.length ? (
            tables.map((table) => (
              <label className="check-row" key={table.name}>
                <input
                  checked={Boolean(table.include)}
                  disabled={table.required || table.protected}
                  onChange={(event) =>
                    setTables((current) =>
                      current.map((item) =>
                        item.name === table.name
                          ? { ...item, include: event.target.checked }
                          : item,
                      ),
                    )
                  }
                  type="checkbox"
                />
                <span>{labelize(table.name)}</span>
                <em>
                  {table.required || table.protected
                    ? "Required"
                    : table.estimated_rows
                      ? `${formatNumber(table.estimated_rows)} rows`
                      : "Optional"}
                </em>
              </label>
            ))
          ) : (
            <p className="muted">This profile does not expose a table manifest yet.</p>
          )}
        </div>
      </section>

      <section className="validation-box">
        <h4>Validation</h4>
        <HealthChecklist items={validation} />
      </section>

      {preview ? (
        <section className="validation-box">
          <h4>Preview</h4>
          <pre>{JSON.stringify(preview, null, 2)}</pre>
        </section>
      ) : null}

      {message ? <p className="form-message">{message}</p> : null}

      <button
        className="primary wide"
        disabled={!canCreate(draft) || busy}
        onClick={() =>
          void onCreateExport?.(
            {
              profile_id: profileId,
              start,
              end,
              output_path: outputPath || undefined,
            },
            draft,
          )
        }
        type="button"
      >
        <Play size={16} />
        Start Export
      </button>
    </aside>
  );
}

function createValidationItems(draft: ExportCreateDraft): HealthItem[] {
  const selectedTables = draft.tables.filter((table) => table.include);
  return [
    {
      id: "profile",
      label: "Profile selected",
      level: draft.profile_id ? "ok" : "critical",
      value: draft.profile_id ? "Ready" : "Missing",
    },
    {
      id: "range",
      label: "Date range",
      level: draft.start && draft.end ? "ok" : "warning",
      value: draft.start && draft.end ? `${draft.start} to ${draft.end}` : "Incomplete",
    },
    {
      id: "tables",
      label: "Included tables",
      level: selectedTables.length ? "ok" : "critical",
      value: selectedTables.length,
    },
    {
      id: "path",
      label: "Output path",
      level: draft.output_path ? "ok" : "warning",
      value: draft.output_path ? "Set" : "Auto",
    },
  ];
}

function canCreate(draft: ExportCreateDraft) {
  return Boolean(draft.profile_id && draft.start && draft.end);
}

function toggleValue(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}
