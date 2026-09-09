import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  Area,
  BarChart,
  Bar,
} from "recharts";

export function EquityCurve({ data }: { data: Array<{ timestamp: string; value: number }> }) {
  if (!data?.length) return null;

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis 
          dataKey="timestamp" 
          tickFormatter={(ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} 
        />
        <YAxis />
        <Tooltip />
        <Legend verticalAlign="top" height={36} />
        <Line type="monotone" dataKey="value" stroke="#22c55e" strokeWidth={2} />
        <Area type="monotone" dataKey="value" fill="#22c55e" fillOpacity={0.1} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function DrawdownChart({ data }: { data: Array<{ timestamp: string; drawdown: number }> }) {
  if (!data?.length) return null;

  // Convert drawdown to negative for proper visualization (drawdown is negative %)
  const chartData = data.map(d => ({ ...d, value: -Math.abs(d.drawdown) }));

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis 
          dataKey="timestamp" 
          tickFormatter={(ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} 
        />
        <YAxis domain={[Math.min(0, Math.min(...chartData.map(d => d.value)) * 1.1), 0]} />
        <Tooltip formatter={(value) => `${Math.abs(value)}%`} />
        <Line type="monotone" dataKey="value" stroke="#ef4444" strokeWidth={2} />
        <Area type="monotone" dataKey="value" fill="#ef4444" fillOpacity={0.2} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function AllocationPieChart({ allocation }: { allocation: { reserve: number; surplus: number } }) {
  const total = allocation.reserve + allocation.surplus;
  if (total <= 0) return null;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart 
        data={[
          { name: "Reserve (80%)", value: allocation.reserve, fill: "#3b82f6" },
          { name: "Surplus (20%)", value: allocation.surplus, fill: "#10b981" },
        ]} 
        margin={{ top: 20, right: 30, left: 0, bottom: 5 }}
      >
        <XAxis dataKey="name" tickLine={false} tickSize={0} />
        <YAxis domain={[0, total * 1.1]} />
        <Tooltip formatter={(value) => `$${value.toLocaleString()}`} />
        <Legend verticalAlign="top" height={36} />
        <Bar dataKey="value" barSize="60" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
