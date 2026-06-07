import {
  BarChart,
  Callout,
  Divider,
  Grid,
  H1,
  H2,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

type Row = {
  date: string;
  day: string;
  spx: number;
  atm: number;
  callStrike: number;
  putStrike: number;
  cost: number;
  pnl12: number;
  pnl330: number;
};

const pm10: Row[] = [
  { date: "2026-05-08", day: "Fri", spx: 7350.7, atm: 7350, callStrike: 7360, putStrike: 7340, cost: 27.65, pnl12: 915, pnl330: 264 },
  { date: "2026-05-11", day: "Mon", spx: 7374.2, atm: 7370, callStrike: 7380, putStrike: 7360, cost: 27.4, pnl12: 1815, pnl330: -153 },
  { date: "2026-05-12", day: "Tue", spx: 7362.3, atm: 7360, callStrike: 7370, putStrike: 7350, cost: 28.1, pnl12: -1337, pnl330: -243 },
  { date: "2026-05-13", day: "Wed", spx: 7373.8, atm: 7370, callStrike: 7380, putStrike: 7360, cost: 27.7, pnl12: 1340, pnl330: 4628 },
  { date: "2026-05-14", day: "Thu", spx: 7445.05, atm: 7450, callStrike: 7460, putStrike: 7440, cost: 26.5, pnl12: 2980, pnl330: 1755 },
  { date: "2026-05-15", day: "Fri", spx: 7407.6, atm: 7410, callStrike: 7420, putStrike: 7400, cost: 34.8, pnl12: -1345, pnl330: -2673 },
  { date: "2026-05-18", day: "Mon", spx: 7396.1, atm: 7400, callStrike: 7410, putStrike: 7390, cost: 28.1, pnl12: -634, pnl330: -1001 },
  { date: "2026-05-19", day: "Tue", spx: 7343.5, atm: 7340, callStrike: 7350, putStrike: 7330, cost: 36.2, pnl12: -1655, pnl330: -2120 },
  { date: "2026-05-20", day: "Wed", spx: 7353.7, atm: 7350, callStrike: 7360, putStrike: 7340, cost: 31.29, pnl12: 3209, pnl330: 3319 },
  { date: "2026-05-21", day: "Thu", spx: 7377.4, atm: 7380, callStrike: 7390, putStrike: 7370, cost: 38.2, pnl12: -660, pnl330: 1668 },
  { date: "2026-05-22", day: "Fri", spx: 7470.8, atm: 7470, callStrike: 7480, putStrike: 7460, cost: 32.1, pnl12: -1050, pnl330: -2760 },
  { date: "2026-05-26", day: "Tue", spx: 7496.2, atm: 7500, callStrike: 7510, putStrike: 7490, cost: 24.94, pnl12: -943, pnl330: -1829 },
  { date: "2026-05-27", day: "Wed", spx: 7508.3, atm: 7510, callStrike: 7520, putStrike: 7500, cost: 24.4, pnl12: -1151, pnl330: -1901 },
  { date: "2026-05-28", day: "Thu", spx: 7499.7, atm: 7500, callStrike: 7510, putStrike: 7490, cost: 24.4, pnl12: 2230, pnl330: 3065 },
  { date: "2026-05-29", day: "Fri", spx: 7560.7, atm: 7560, callStrike: 7570, putStrike: 7550, cost: 24.17, pnl12: -410, pnl330: -740 },
  { date: "2026-06-01", day: "Mon", spx: 7553.0, atm: 7550, callStrike: 7560, putStrike: 7540, cost: 28.6, pnl12: 158, pnl330: 2490 },
  { date: "2026-06-02", day: "Tue", spx: 7575.56, atm: 7580, callStrike: 7590, putStrike: 7570, cost: 19.46, pnl12: 690, pnl330: 111 },
  { date: "2026-06-03", day: "Wed", spx: 7585.8, atm: 7590, callStrike: 7600, putStrike: 7580, cost: 18.57, pnl12: -339, pnl330: -284 },
  { date: "2026-06-04", day: "Thu", spx: 7519.9, atm: 7520, callStrike: 7530, putStrike: 7510, cost: 26.7, pnl12: 1337, pnl330: 4055 },
];

const pm20: Row[] = [
  { date: "2026-05-08", day: "Fri", spx: 7350.7, atm: 7350, callStrike: 7370, putStrike: 7330, cost: 19.68, pnl12: 789, pnl330: 87 },
  { date: "2026-05-11", day: "Mon", spx: 7374.2, atm: 7370, callStrike: 7390, putStrike: 7350, cost: 19.6, pnl12: 1573, pnl330: -223 },
  { date: "2026-05-12", day: "Tue", spx: 7362.3, atm: 7360, callStrike: 7380, putStrike: 7340, cost: 21.0, pnl12: -1170, pnl330: -516 },
  { date: "2026-05-13", day: "Wed", spx: 7373.8, atm: 7370, callStrike: 7390, putStrike: 7350, cost: 19.9, pnl12: 1285, pnl330: 4315 },
  { date: "2026-05-14", day: "Thu", spx: 7445.05, atm: 7450, callStrike: 7470, putStrike: 7430, cost: 18.65, pnl12: 2780, pnl330: 1500 },
  { date: "2026-05-15", day: "Fri", spx: 7407.6, atm: 7410, callStrike: 7430, putStrike: 7390, cost: 26.9, pnl12: -1250, pnl330: -2420 },
  { date: "2026-05-18", day: "Mon", spx: 7396.1, atm: 7400, callStrike: 7420, putStrike: 7380, cost: 20.8, pnl12: -555, pnl330: -1095 },
  { date: "2026-05-19", day: "Tue", spx: 7343.5, atm: 7340, callStrike: 7360, putStrike: 7320, cost: 28.02, pnl12: -1482, pnl330: -2075 },
  { date: "2026-05-20", day: "Wed", spx: 7353.7, atm: 7350, callStrike: 7370, putStrike: 7330, cost: 23.5, pnl12: 2948, pnl330: 3425 },
  { date: "2026-05-21", day: "Thu", spx: 7377.4, atm: 7380, callStrike: 7400, putStrike: 7360, cost: 30.2, pnl12: -680, pnl330: 1388 },
  { date: "2026-05-22", day: "Fri", spx: 7470.8, atm: 7470, callStrike: 7490, putStrike: 7450, cost: 24.2, pnl12: -1010, pnl330: -2280 },
  { date: "2026-05-26", day: "Tue", spx: 7496.2, atm: 7500, callStrike: 7520, putStrike: 7480, cost: 17.72, pnl12: -822, pnl330: -1590 },
  { date: "2026-05-27", day: "Wed", spx: 7508.3, atm: 7510, callStrike: 7530, putStrike: 7490, cost: 17.2, pnl12: -975, pnl330: -1618 },
  { date: "2026-05-28", day: "Thu", spx: 7499.7, atm: 7500, callStrike: 7520, putStrike: 7480, cost: 16.0, pnl12: 2314, pnl330: 2933 },
  { date: "2026-05-29", day: "Fri", spx: 7560.7, atm: 7560, callStrike: 7580, putStrike: 7540, cost: 16.85, pnl12: -425, pnl330: -830 },
  { date: "2026-06-01", day: "Mon", spx: 7553.0, atm: 7550, callStrike: 7570, putStrike: 7530, cost: 23.86, pnl12: -264, pnl330: 1960 },
  { date: "2026-06-02", day: "Tue", spx: 7575.56, atm: 7580, callStrike: 7600, putStrike: 7560, cost: 12.82, pnl12: 464, pnl330: -220 },
  { date: "2026-06-03", day: "Wed", spx: 7585.8, atm: 7590, callStrike: 7610, putStrike: 7570, cost: 11.82, pnl12: -297, pnl330: -509 },
  { date: "2026-06-04", day: "Thu", spx: 7519.9, atm: 7520, callStrike: 7540, putStrike: 7500, cost: 18.9, pnl12: 1315, pnl330: 3803 },
];

function fmtDollar(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  return `$${sign}${Math.abs(Math.round(v)).toLocaleString()}`;
}

function wins(rows: Row[], key: "pnl12" | "pnl330"): number {
  return rows.filter((r) => r[key] > 0).length;
}

function total(rows: Row[], key: "pnl12" | "pnl330"): number {
  return rows.reduce((s, r) => s + r[key], 0);
}

function avgCost(rows: Row[]): number {
  return rows.reduce((s, r) => s + r.cost, 0) / rows.length;
}

function buildTableRows(rows: Row[]) {
  const tableRows = rows.map((r) => [
    r.date.slice(5),
    r.day,
    r.atm.toLocaleString(),
    `${r.callStrike}/${r.putStrike}`,
    `$${r.cost.toFixed(2)}`,
    fmtDollar(r.pnl12),
    fmtDollar(r.pnl330),
  ]);

  tableRows.push([
    "",
    "GRAND TOTAL",
    "",
    "",
    "",
    fmtDollar(total(rows, "pnl12")),
    fmtDollar(total(rows, "pnl330")),
  ]);

  const rowTone = rows.map((r) =>
    r.pnl330 > 0 ? ("success" as const) : r.pnl330 < -1000 ? ("danger" as const) : undefined,
  );
  rowTone.push("neutral");

  return { tableRows, rowTone };
}

export default function SPXStrangleLast20d() {
  const pm10Table = buildTableRows(pm10);
  const pm20Table = buildTableRows(pm20);

  const chartData = pm10.map((r, i) => ({
    label: r.date.slice(5),
    pm10_12: r.pnl12,
    pm10_330: r.pnl330,
    pm20_12: pm20[i].pnl12,
    pm20_330: pm20[i].pnl330,
  }));

  return (
    <Stack gap={24}>
      <Stack gap={4}>
        <H1>SPX 0DTE Strangles — Last 20 Days</H1>
        <Text tone="secondary">
          Entry 9:31 AM ET · SPX = SPY × 10 · Buy 1 call + 1 put · $100 multiplier ·
          May 8 – Jun 4, 2026 (19 sessions)
        </Text>
      </Stack>

      <Grid columns={4} gap={12}>
        <Stat value="±$10" label="Call ATM+10 / Put ATM−10" />
        <Stat
          value={fmtDollar(total(pm10, "pnl12"))}
          label="±10 · 12:00 PM total"
          tone={total(pm10, "pnl12") >= 0 ? "success" : "danger"}
        />
        <Stat
          value={fmtDollar(total(pm10, "pnl330"))}
          label="±10 · 3:30 PM total"
          tone={total(pm10, "pnl330") >= 0 ? "success" : "danger"}
        />
        <Stat
          value={`${wins(pm10, "pnl330")}/19`}
          label="±10 · 3:30 PM win days"
        />
      </Grid>

      <Grid columns={4} gap={12}>
        <Stat value="±$20" label="Call ATM+20 / Put ATM−20" />
        <Stat
          value={fmtDollar(total(pm20, "pnl12"))}
          label="±20 · 12:00 PM total"
          tone={total(pm20, "pnl12") >= 0 ? "success" : "danger"}
        />
        <Stat
          value={fmtDollar(total(pm20, "pnl330"))}
          label="±20 · 3:30 PM total"
          tone={total(pm20, "pnl330") >= 0 ? "success" : "danger"}
        />
        <Stat
          value={`${wins(pm20, "pnl330")}/19`}
          label="±20 · 3:30 PM win days"
        />
      </Grid>

      <Callout tone="info">
        Wider wings (±$20) cost ~26% less to enter ($20.40 vs $27.86 avg) but capture
        less on big trend days. ±$10 outperformed at both checkpoints over this window.
      </Callout>

      <Divider />

      <H2>±$10 Strangle — 12:00 PM & 3:30 PM P/L</H2>
      <Text tone="tertiary" size="small">
        Avg entry cost ${avgCost(pm10).toFixed(2)}/contract · Source: Polygon SPXW 1m bars
      </Text>
      <Table
        headers={["Date", "Day", "ATM", "C/P Strikes", "Cost", "12:00 PM", "3:30 PM"]}
        rows={pm10Table.tableRows}
        columnAlign={["left", "left", "right", "center", "right", "right", "right"]}
        rowTone={pm10Table.rowTone}
        striped
        stickyHeader
      />

      <Divider />

      <H2>±$20 Strangle — 12:00 PM & 3:30 PM P/L</H2>
      <Text tone="tertiary" size="small">
        Avg entry cost ${avgCost(pm20).toFixed(2)}/contract · Source: Polygon SPXW 1m bars
      </Text>
      <Table
        headers={["Date", "Day", "ATM", "C/P Strikes", "Cost", "12:00 PM", "3:30 PM"]}
        rows={pm20Table.tableRows}
        columnAlign={["left", "left", "right", "center", "right", "right", "right"]}
        rowTone={pm20Table.rowTone}
        striped
        stickyHeader
      />

      <Divider />

      <H2>Daily P/L Comparison</H2>
      <Text tone="tertiary" size="small">
        Combined strangle P/L ($) by session · Source: Polygon · May 8 – Jun 4, 2026
      </Text>
      <BarChart
        categories={chartData.map((d) => d.label)}
        series={[
          { name: "±10 @ 12pm", data: chartData.map((d) => d.pm10_12), tone: "info" },
          { name: "±10 @ 3:30pm", data: chartData.map((d) => d.pm10_330), tone: "success" },
          { name: "±20 @ 12pm", data: chartData.map((d) => d.pm20_12), tone: "warning" },
          { name: "±20 @ 3:30pm", data: chartData.map((d) => d.pm20_330), tone: "neutral" },
        ]}
        height={280}
      />
    </Stack>
  );
}
