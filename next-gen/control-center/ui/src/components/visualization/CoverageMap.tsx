import { useMemo } from "react";
import type { KeyboardEvent, ReactNode } from "react";

import type { CityCoverage } from "./types";
import "./visualization.css";

export interface CoverageMapProps {
  cities: CityCoverage[];
  selectedCityId?: string;
  title?: string;
  subtitle?: string;
  height?: number | string;
  onCitySelect?: (city: CityCoverage) => void;
  renderMapLayer?: (props: CoverageMapRenderProps) => ReactNode;
}

export interface CoverageMapRenderProps {
  cities: CityCoverage[];
  selectedCityId?: string;
  onCitySelect?: (city: CityCoverage) => void;
}

const defaultCityPositions: Record<string, { x: number; y: number }> = {
  aus: { x: 43, y: 70 },
  den: { x: 36, y: 45 },
  la: { x: 18, y: 62 },
  mia: { x: 74, y: 82 },
  nyc: { x: 78, y: 35 },
  okc: { x: 48, y: 62 },
};

const normalizeCityCode = (city: CityCoverage) =>
  (city.code ?? city.id ?? city.label).toLowerCase();

const projectCity = (city: CityCoverage) => {
  const code = normalizeCityCode(city);
  if (defaultCityPositions[code]) {
    return defaultCityPositions[code];
  }

  if (typeof city.lat === "number" && typeof city.lon === "number") {
    return {
      x: ((city.lon + 125) / 59) * 100,
      y: ((49 - city.lat) / 25) * 100,
    };
  }

  return { x: 50, y: 50 };
};

const radiusFor = (city: CityCoverage) => {
  const max = city.max ?? 100;
  const ratio = max > 0 ? Math.max(0, Math.min(1, city.value / max)) : 0;
  return 7 + ratio * 11;
};

const handleCityKeyDown = (
  event: KeyboardEvent<SVGGElement>,
  city: CityCoverage,
  onCitySelect?: (city: CityCoverage) => void,
) => {
  if (city.disabled) {
    return;
  }
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    onCitySelect?.(city);
  }
};

export function CoverageMap({
  cities,
  selectedCityId,
  title = "City Coverage",
  subtitle,
  height = 360,
  onCitySelect,
  renderMapLayer,
}: CoverageMapProps) {
  const positionedCities = useMemo(
    () =>
      cities.map((city) => ({
        city,
        position: projectCity(city),
        radius: radiusFor(city),
      })),
    [cities],
  );

  if (renderMapLayer) {
    return (
      <section className="kbc-coverage-map" style={{ minHeight: height }}>
        <header className="kbc-coverage-map__header">
          <div>
            <h3>{title}</h3>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
        </header>
        <div className="kbc-coverage-map__stage">
          {renderMapLayer({ cities, selectedCityId, onCitySelect })}
        </div>
      </section>
    );
  }

  return (
    <section className="kbc-coverage-map" style={{ minHeight: height }}>
      <header className="kbc-coverage-map__header">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        <span>{cities.length} cities</span>
      </header>

      <div className="kbc-coverage-map__stage">
        <svg viewBox="0 0 100 100" className="kbc-coverage-map__svg" role="img" aria-label={title}>
          <path
            className="kbc-coverage-map__outline"
            d="M12 55 C17 34 34 26 52 25 C68 23 83 33 88 49 C93 65 82 81 63 84 C42 88 19 78 12 55 Z"
          />
          <path className="kbc-coverage-map__grid" d="M20 42 H84 M18 62 H88 M34 29 V82 M58 26 V86 M78 36 V77" />
          {positionedCities.map(({ city, position, radius }) => {
            const selected = city.selected || city.id === selectedCityId;
            const className = [
              "kbc-coverage-map__city",
              selected ? "is-selected" : "",
              city.disabled ? "is-disabled" : "",
              `is-${city.status ?? "neutral"}`,
            ]
              .filter(Boolean)
              .join(" ");

            return (
              <g
                key={city.id}
                className={className}
                role="button"
                tabIndex={city.disabled ? -1 : 0}
                aria-label={`Select ${city.label}`}
                aria-disabled={city.disabled}
                onClick={() => {
                  if (!city.disabled) {
                    onCitySelect?.(city);
                  }
                }}
                onKeyDown={(event) => handleCityKeyDown(event, city, onCitySelect)}
              >
                <circle
                  className="kbc-coverage-map__hit"
                  cx={position.x}
                  cy={position.y}
                  r={radius}
                >
                  <title>
                    {city.label}: {city.value.toLocaleString()}
                  </title>
                </circle>
                <text x={position.x} y={position.y + radius + 6} textAnchor="middle">
                  {city.code ?? city.label}
                </text>
              </g>
            );
          })}
        </svg>

        <div className="kbc-coverage-map__list">
          {cities.map((city) => {
            const max = city.max ?? 100;
            const width = max > 0 ? Math.max(0, Math.min(100, (city.value / max) * 100)) : 0;
            const selected = city.selected || city.id === selectedCityId;
            return (
              <button
                key={city.id}
                type="button"
                className={selected ? "kbc-coverage-map__city-row is-selected" : "kbc-coverage-map__city-row"}
                disabled={city.disabled}
                onClick={() => onCitySelect?.(city)}
              >
                <span>{city.label}</span>
                <span className="kbc-coverage-map__mini-track" aria-hidden="true">
                  <span style={{ width: `${width}%` }} />
                </span>
                <strong>{city.value.toLocaleString()}</strong>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
