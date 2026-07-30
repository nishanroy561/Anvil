"use client";

import clsx from "clsx";
import type { GraderInfo } from "@/lib/types";

export type GraderSelection = Record<string, Record<string, unknown>>;

/**
 * Registry-driven picker (plan/plugins.md §2): the cards and their config
 * forms are generated from `GET /v1/graders` — an installed grader plugin
 * appears here with zero frontend changes. The form renderer covers the JSON
 * Schema subset the catalog uses: string, number, boolean, enum, string[].
 */
export function GraderPicker({
  graders,
  selection,
  onChange,
}: {
  graders: GraderInfo[];
  selection: GraderSelection;
  onChange: (next: GraderSelection) => void;
}) {
  const toggle = (name: string) => {
    const next = { ...selection };
    if (name in next) delete next[name];
    else next[name] = {};
    onChange(next);
  };

  const setConfig = (name: string, key: string, value: unknown) => {
    onChange({ ...selection, [name]: { ...selection[name], [key]: value } });
  };

  const groups: [string, GraderInfo[]][] = [
    ["Deterministic — free, instant", graders.filter((g) => g.kind === "deterministic")],
    ["LLM judges — cost per item", graders.filter((g) => g.kind === "judge")],
  ];

  return (
    <div className="flex flex-col gap-4">
      {groups.map(([label, group]) => (
        <section key={label}>
          <h3 className="mb-2 font-mono text-2xs uppercase tracking-wider text-ink-faint">{label}</h3>
          <div className="grid gap-2 md:grid-cols-2">
            {group.map((grader) => {
              const active = grader.name in selection;
              return (
                <div
                  key={grader.name}
                  className={clsx(
                    "rounded-lg border p-3 transition-colors duration-150",
                    active ? "border-ember/60 bg-ember/5" : "border-line bg-iron-900",
                  )}
                >
                  <label className="flex cursor-pointer items-start gap-2.5">
                    <input
                      type="checkbox"
                      checked={active}
                      onChange={() => toggle(grader.name)}
                      className="mt-0.5 h-3.5 w-3.5 accent-[#3B76FF]"
                    />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 font-mono text-[13px] font-medium text-ink">
                        {grader.name}
                        {/* Says where an unfamiliar name came from. Someone
                            reviewing a run config should not have to guess
                            whether a grader is Anvil's or a local install. */}
                        {grader.source === "plugin" && (
                          <span
                            className="rounded-full border border-line px-1.5 py-px text-2xs font-normal text-ink-faint"
                            title="Provided by a plugin installed in the worker"
                          >
                            plugin
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block text-2xs leading-relaxed text-ink-muted">
                        {grader.description}
                      </span>
                    </span>
                  </label>

                  {active && <ConfigForm grader={grader} config={selection[grader.name]} onSet={setConfig} />}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function ConfigForm({
  grader,
  config,
  onSet,
}: {
  grader: GraderInfo;
  config: Record<string, unknown>;
  onSet: (grader: string, key: string, value: unknown) => void;
}) {
  const properties = grader.config_schema.properties ?? {};
  const required = grader.config_schema.required ?? [];
  const keys = Object.keys(properties);
  if (keys.length === 0) return null;

  return (
    <div className="mt-2.5 flex flex-col gap-2 border-t border-line/60 pt-2.5">
      {keys.map((key) => {
        const prop = properties[key];
        const value = config[key];
        const label = (
          <span className="text-2xs font-medium text-ink-muted">
            {prop.title ?? key}
            {required.includes(key) && <span className="text-ember"> *</span>}
          </span>
        );

        if (prop.type === "boolean") {
          return (
            <label key={key} className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={Boolean(value ?? prop.default ?? false)}
                onChange={(e) => onSet(grader.name, key, e.target.checked)}
                className="h-3 w-3 accent-[#3B76FF]"
              />
              {label}
            </label>
          );
        }

        if (prop.enum) {
          return (
            <label key={key} className="flex flex-col gap-1">
              {label}
              <select
                value={String(value ?? prop.default ?? prop.enum[0])}
                onChange={(e) => onSet(grader.name, key, e.target.value)}
                className="field h-7 cursor-pointer text-2xs"
              >
                {prop.enum.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          );
        }

        if (prop.type === "number" || prop.type === "integer") {
          return (
            <label key={key} className="flex flex-col gap-1">
              {label}
              <input
                type="number"
                step={prop.type === "integer" ? 1 : 0.05}
                min={prop.minimum}
                max={prop.maximum}
                value={value === undefined ? String(prop.default ?? "") : String(value)}
                onChange={(e) =>
                  onSet(grader.name, key, e.target.value === "" ? undefined : Number(e.target.value))
                }
                className="field h-7 text-2xs"
              />
            </label>
          );
        }

        if (prop.type === "array") {
          return (
            <label key={key} className="flex flex-col gap-1">
              {label}
              <input
                value={Array.isArray(value) ? value.join(", ") : ""}
                onChange={(e) =>
                  onSet(
                    grader.name,
                    key,
                    e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                placeholder="comma, separated"
                className="field h-7 text-2xs"
              />
            </label>
          );
        }

        const isLong = key === "rubric";
        return (
          <label key={key} className="flex flex-col gap-1">
            {label}
            {isLong ? (
              <textarea
                value={String(value ?? "")}
                onChange={(e) => onSet(grader.name, key, e.target.value)}
                rows={3}
                placeholder={prop.description}
                className="rounded-md border border-line bg-iron-950 p-2 font-mono text-2xs text-ink placeholder:text-ink-faint focus:border-ember focus:outline-none"
              />
            ) : (
              <input
                value={String(value ?? "")}
                onChange={(e) => onSet(grader.name, key, e.target.value)}
                placeholder={prop.description}
                className="field h-7 text-2xs"
              />
            )}
          </label>
        );
      })}
    </div>
  );
}
