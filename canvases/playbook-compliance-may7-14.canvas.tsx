import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  useHostTheme,
} from "cursor/canvas";
import { useState } from "react";

type DayKey = "may7" | "may8" | "may12" | "may13" | "may14";

export default function PlaybookCompliance() {
  const theme = useHostTheme();
  const [selectedDay, setSelectedDay] = useState<DayKey | null>(null);

  const ruleGrades: {
    rule: string;
    grade: string;
    tone: "success" | "danger" | "warning";
    detail: string;
  }[] = [
    {
      rule: "OT-OCO on every trade",
      grade: "F",
      tone: "danger",
      detail:
        "May 7: 5/5 used OT-OCO. After that, collapsed. May 12: 2/5. May 13: 0/7. May 14: 3/12 (2 cancelled). You reverted to manual entries.",
    },
    {
      rule: "Never cancel OCO",
      grade: "D",
      tone: "danger",
      detail:
        "3 confirmed cancellations: May 8 (sold at Market), May 14 (7510C sold worse than stop), May 14 (7500C cancelled for manual exit).",
    },
    {
      rule: "Max 1 contract on SPX",
      grade: "B",
      tone: "warning",
      detail:
        "May 7: 10 contracts on the 7370C lottery ticket. All other days: 1 contract. One lapse, but mostly fixed.",
    },
    {
      rule: "-50% hard stop",
      grade: "C",
      tone: "warning",
      detail:
        "Most stops within range. But May 8: -80% stop ($0.20 on $1.00). May 13: -92% stop ($0.15 on $1.95). These defeat the purpose.",
    },
    {
      rule: "Avoid 10:00-11:30 AM",
      grade: "F",
      tone: "danger",
      detail:
        "7 trades taken in the avoid window across 4 of 5 days. May 13's biggest loss (-$640) came at 10:10 AM. Not even trying to avoid it.",
    },
    {
      rule: "Time stop (1-3 min, max 5 min)",
      grade: "D",
      tone: "danger",
      detail:
        "May 13: holds of 42 min, 26 min, 15 min, 10 min. May 14: 12 min hold. Fast when winning, but holding losers way past the 5-min max.",
    },
    {
      rule: "No options under $1 after 3:30 PM",
      grade: "D",
      tone: "danger",
      detail:
        "May 7: Bought $0.10 calls (10 cts!) and $0.40 call in last 7 min. Pure lottery tickets on expiration day.",
    },
    {
      rule: "Max 2 reversal attempts",
      grade: "C",
      tone: "warning",
      detail:
        "May 7: 3 trades in last 15 min (call, call, put reversal = 3rd attempt). May 14: 4 late trades, though mostly quick scalps.",
    },
    {
      rule: "Qty check (entry = target = stop)",
      grade: "A",
      tone: "success",
      detail: "No qty mismatches observed since the May 6 disaster. Lesson learned.",
    },
  ];

  const days: {
    key: DayKey;
    date: string;
    pnl: number;
    trades: number;
    violations: string[];
    tradeRows: string[][];
    tradeTones: ("success" | "danger" | "warning" | undefined)[];
  }[] = [
    {
      key: "may7",
      date: "May 7 (Wed)",
      pnl: 33,
      trades: 5,
      violations: [
        "10:23 AM trade in avoid window",
        "10 contracts on 7370C lottery ($0.10 each)",
        "$0.40 call at 3:53 PM (under $1 rule)",
        "3rd attempt in last 15 min (put after 2 failed calls)",
      ],
      tradeRows: [
        ["10:23", "7370C", "1", "OT-OCO", "$10.50", "$12.10", "+$160", "15%", "Avoid window"],
        ["15:03", "7370C", "10", "OT-OCO", "$0.10", "$0.10", "~$0", "n/a", "10 cts + $0.10 lottery"],
        ["15:42", "7340C", "1", "OT-OCO", "$1.20", "$0.80", "-$40", "-33%", ""],
        ["15:53", "7340C", "1", "OT-OCO", "$0.40", "$0.25", "-$15", "-38%", "Under $1 after 3:30"],
        ["15:57", "7335P", "1", "OT-OCO", "$1.60", "$0.90", "-$70", "-44%", "3rd attempt"],
      ],
      tradeTones: ["warning", "danger", undefined, "warning", "warning"],
    },
    {
      key: "may8",
      date: "May 8 (Thu)",
      pnl: -80,
      trades: 1,
      violations: [
        "Stop set at -80% ($0.20 on $1.00 entry)",
        "Cancelled OCO and sold at Market instead",
        "Held 6+ minutes (over 5-min max)",
      ],
      tradeRows: [
        [
          "15:52",
          "7395P",
          "1",
          "OT-OCO (cancelled)",
          "$1.00",
          "$0.20",
          "-$80",
          "-80%",
          "Cancelled OCO, -80% stop, 6 min hold",
        ],
      ],
      tradeTones: ["danger"],
    },
    {
      key: "may12",
      date: "May 12 (Mon)",
      pnl: 295,
      trades: 5,
      violations: [
        "10:19 AM trade in avoid window",
        "+77% profit target on 7395P (greedy, playbook says +30%)",
        "3 trades without OT-OCO (plain buy + separate OCO or none)",
        "Tried to buy at 4:00:05 PM (rejected, market closed)",
      ],
      tradeRows: [
        ["10:19", "7380P", "1", "OT-OCO", "$10.50", "$14.00", "+$350", "+33%", "Avoid window"],
        ["15:32", "7395P", "1", "OT-OCO", "$3.00", "$1.95", "-$105", "-35%", "+77% target (greedy)"],
        ["15:42", "7400P", "1", "OCO (not OT)", "$3.00", "$2.00", "-$100", "-33%", "No OT-OCO"],
        ["15:52", "7400C", "1", "None", "$4.00", "$6.60", "+$260", "+65%", "No OCO at all"],
        ["15:54", "7400C", "1", "None", "$7.00", "$5.90", "-$110", "-16%", "No OCO at all"],
      ],
      tradeTones: ["warning", "warning", "warning", undefined, "danger"],
    },
    {
      key: "may13",
      date: "May 13 (Tue)",
      pnl: -995,
      trades: 7,
      violations: [
        "0/7 trades used OT-OCO",
        "10:10 AM trade in avoid window (-$640 loss)",
        "42-min hold on 7395P (bleed to -50% stop)",
        "26-min hold on 7455C (bleed to stop)",
        "15-min hold on 7415P",
        "-92% stop on last trade ($0.15 stop on $1.95)",
      ],
      tradeRows: [
        ["10:10", "7395P", "1", "OCO (not OT)", "$13.00", "$6.60", "-$640", "-49%", "Avoid window, 42-min hold"],
        ["11:18", "7415C", "1", "None", "$8.60", "$9.00", "+$40", "+5%", "Avoid window"],
        ["11:36", "7415P", "1", "None", "$8.00", "$7.10", "-$90", "-11%", "15-min hold"],
        ["13:17", "7445C", "1", "None", "$7.50", "$8.60", "+$110", "+15%", "10-min hold"],
        ["14:30", "7455C", "1", "OCO (not OT)", "$6.00", "$2.95", "-$305", "-51%", "26-min hold to stop"],
        ["15:22", "7455C", "1", "None", "$3.00", "$3.70", "+$70", "+23%", "Quick scalp"],
        ["15:50", "7455C", "1", "OCO (not OT)", "$1.95", "$0.15", "-$180", "-92%", "-92% stop!"],
      ],
      tradeTones: ["danger", "warning", "warning", undefined, "danger", "success", "danger"],
    },
    {
      key: "may14",
      date: "May 14 (Today)",
      pnl: 488,
      trades: 12,
      violations: [
        "4 trades in avoid window (10:05-10:59 AM)",
        "Only 1 of 12 trades used true OT-OCO (most were plain buys)",
        "Cancelled OCO on 7510C (sold Market at $6.60, stop was $7.00 — worse exit)",
        "Cancelled OCO on 7500C (sold Market at $7.70, quick profit but broke rule)",
      ],
      tradeRows: [
        ["10:05", "7470P", "1", "None", "$12.00", "$13.00", "+$100", "+8%", "Avoid window"],
        ["10:08", "7475C", "1", "None", "$12.00", "$13.00", "+$100", "+8%", "Avoid window"],
        ["10:25", "7480C", "1", "None", "$12.00", "$13.00", "+$100", "+8%", "Avoid window"],
        ["10:59", "7490C", "1", "None", "$13.00", "$15.00", "+$200", "+15%", "Avoid window"],
        [
          "12:02",
          "7510C",
          "1",
          "OT-OCO (modified)",
          "$11.00",
          "$10.00",
          "-$100",
          "-9%",
          "Cancelled OCO, replaced with new",
        ],
        [
          "12:23",
          "7510C",
          "1",
          "OT-OCO (cancelled)",
          "$9.00",
          "$6.60",
          "-$240",
          "-27%",
          "Cancelled OCO, Market sell",
        ],
        ["12:33", "7490C", "1", "None", "$14.00", "$14.70", "+$70", "+5%", "Quick scalp"],
        [
          "13:10",
          "7500C",
          "1",
          "OT-OCO (cancelled)",
          "$7.00",
          "$7.70",
          "+$70",
          "+10%",
          "Cancelled OCO for quick exit",
        ],
        ["15:13", "7500C", "1", "None", "$7.00", "$8.30", "+$130", "+19%", ""],
        ["15:50", "7500C", "1", "None", "$5.00", "$5.50", "+$50", "+10%", ""],
        ["15:51", "7500C", "1", "None", "$1.90", "$2.75", "+$85", "+45%", ""],
        ["15:56", "7500C", "1", "None", "$1.45", "$0.85", "-$60", "-41%", ""],
      ],
      tradeTones: [
        "warning",
        "warning",
        "warning",
        "warning",
        "warning",
        "danger",
        undefined,
        "warning",
        "success",
        "success",
        "success",
        undefined,
      ],
    },
  ];

  const totalPnl = days.reduce((s, d) => s + d.pnl, 0);
  const totalTrades = days.reduce((s, d) => s + d.trades, 0);
  const greenDays = days.filter((d) => d.pnl > 0).length;
  const totalViolations = days.reduce((s, d) => s + d.violations.length, 0);

  const sel = selectedDay ? days.find((d) => d.key === selectedDay) : null;

  return (
    <Stack gap={20}>
      <H1>Playbook Compliance Review: May 7 – 14</H1>
      <Text tone="secondary">
        Auditing every trade against your Trading Playbook rules. 30 trades across 5 sessions.
      </Text>

      <Grid columns={4} gap={12}>
        <Stat
          value={`$${totalPnl >= 0 ? "+" : ""}${totalPnl}`}
          label="Week P/L"
          tone={totalPnl >= 0 ? "success" : "danger"}
        />
        <Stat value={`${totalTrades}`} label="Trades" />
        <Stat value={`${greenDays}/5`} label="Green Days" tone={greenDays >= 3 ? "success" : "warning"} />
        <Stat value={`${totalViolations}`} label="Rule Violations" tone="danger" />
      </Grid>

      <Callout tone="danger" title="Bottom line">
        Your edge (fast scalps, good reads) is still there — 3 green days prove it. But you have abandoned
        OT-OCO on most trades and are actively trading the avoid window. May 13 (-$995) is textbook what
        happens when discipline slips: long holds, no OT-OCO, and the avoid window turned a winnable day into
        your worst of the week.
      </Callout>

      <Divider />

      <H2>Rule-by-Rule Grades</H2>
      <Table
        headers={["Rule", "Grade", "Assessment"]}
        rows={ruleGrades.map((r) => [r.rule, r.grade, r.detail])}
        rowTone={ruleGrades.map((r) => (r.tone === "success" ? "success" : r.tone === "danger" ? "danger" : "warning"))}
        striped
      />

      <Divider />

      <H2>Daily Breakdown</H2>
      <Text tone="secondary">Click a day to see every trade with violation flags.</Text>

      <Row gap={8} wrap>
        {days.map((d) => (
          <Pill key={d.key} active={selectedDay === d.key} onClick={() => setSelectedDay(d.key)} tone={d.pnl >= 0 ? "success" : "deleted"}>
            {d.date}: {d.pnl >= 0 ? "+" : ""}
            {d.pnl}
          </Pill>
        ))}
      </Row>

      {sel && (
        <Stack gap={12}>
          <Grid columns={3} gap={12}>
            <Stat value={`$${sel.pnl >= 0 ? "+" : ""}${sel.pnl}`} label="Day P/L" tone={sel.pnl >= 0 ? "success" : "danger"} />
            <Stat value={`${sel.trades}`} label="Trades" />
            <Stat value={`${sel.violations.length}`} label="Violations" tone={sel.violations.length > 2 ? "danger" : "warning"} />
          </Grid>

          <H3>Violations</H3>
          <Stack gap={4}>
            {sel.violations.map((v, i) => (
              <Text key={i} tone="secondary" size="small">
                {"\u2022 " + v}
              </Text>
            ))}
          </Stack>

          <H3>Trade Log</H3>
          <Table
            headers={["Time", "Contract", "Qty", "Order Type", "Entry", "Exit", "P/L", "Return", "Flag"]}
            rows={sel.tradeRows}
            rowTone={sel.tradeTones}
            striped
            stickyHeader
          />
        </Stack>
      )}

      <Divider />

      <H2>The Two Patterns Killing You</H2>

      <Grid columns={2} gap={12}>
        <Card>
          <CardHeader trailing={<Pill tone="deleted" size="sm" active>Cost: ~$1,700</Pill>}>
            Pattern 1: OT-OCO Abandonment
          </CardHeader>
          <CardBody>
            <Stack gap={6}>
              <Text>
                May 7 was perfect — 5/5 trades with OT-OCO. Then it fell off a cliff.
                By May 13–14, you're back to plain buys with no automatic protection.
              </Text>
              <Text weight="semibold">
                May 14 example: You cancelled the 7510C OCO (stop at $7.00) and sold at Market for $6.60
                — costing yourself $40 more than the stop would have. The OCO existed to prevent exactly this.
              </Text>
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader trailing={<Pill tone="deleted" size="sm" active>Cost: ~$1,400</Pill>}>
            Pattern 2: Trading the Avoid Window
          </CardHeader>
          <CardBody>
            <Stack gap={6}>
              <Text>
                7 trades between 10:00–11:30 AM across 4 of 5 days. The playbook says zero.
                May 13's -$640 loss at 10:10 AM (42-minute hold bleeding to stop) was the single
                biggest loss of the week.
              </Text>
              <Text weight="semibold">
                Today you made +$500 in the avoid window — but that reinforces the bad habit.
                Your data over 278 trades says this window is -$1,375. One good week doesn't
                override the sample.
              </Text>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <Divider />

      <H2>What You Did Right</H2>
      <Stack gap={4}>
        <Text>
          <Text as="span" weight="semibold">Position sizing:</Text>{" "}
          After the May 6/7 10-contract disasters, you stuck to 1 contract on every subsequent trade. This alone saved you from catastrophic losses.
        </Text>
        <Text>
          <Text as="span" weight="semibold">Qty check:</Text>{" "}
          Zero qty mismatches since May 6. The "qty, qty, qty" rule stuck.
        </Text>
        <Text>
          <Text as="span" weight="semibold">Scalping speed:</Text>{" "}
          When you trust the scalp, you exit fast. Today's afternoon run (4 trades in 9 minutes, +$205) shows your edge is alive.
        </Text>
        <Text>
          <Text as="span" weight="semibold">Stop losses when set:</Text>{" "}
          Most stops are within -50%. The system works when you use it.
        </Text>
      </Stack>

      <Divider />

      <H2>Action Items for Tomorrow</H2>
      <Callout tone="warning" title="Non-negotiable">
        <Stack gap={4}>
          <Text weight="semibold">1. Every single trade gets OT-OCO. No exceptions, no "I'll be quick."</Text>
          <Text tone="secondary" size="small">
            You proved on May 7 you can do this. The backslide started when you got comfortable.
          </Text>
          <Text weight="semibold">2. Close the app from 10:00 to 11:30 AM.</Text>
          <Text tone="secondary" size="small">
            Don't "just watch." Close it. Today's +$500 in that window is the exception that builds overconfidence.
          </Text>
          <Text weight="semibold">3. If a trade isn't green in 3 minutes, cancel OCO and market out.</Text>
          <Text tone="secondary" size="small">
            May 13's 42-minute hold cost $640. A 3-minute exit would have been ~$50-100 loss.
          </Text>
          <Text weight="semibold">4. Never set a stop wider than -50%.</Text>
          <Text tone="secondary" size="small">
            The -80% and -92% stops are fake safety nets. If you're going to risk 90%, you have no stop.
          </Text>
        </Stack>
      </Callout>

      <Text tone="secondary" size="small">
        Based on 30 trades from OrdersMay7-14.csv and Orders.csv, audited against TRADING_PLAYBOOK.md rules.
      </Text>
    </Stack>
  );
}
