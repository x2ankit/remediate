import React, { useState, useEffect } from 'react';
import { Activity, CheckCircle, XCircle, AlertTriangle, IndianRupee } from 'lucide-react';

export default function App() {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    // 1. Fetch historical data on load
    fetch('/api/results')
      .then(res => res.json())
      .then(data => setEvents(data))
      .catch(console.error);

    // 2. Poll for updates every 3 seconds
    const interval = setInterval(() => {
      fetch('/api/results')
        .then(res => {
          if (res.ok) setConnected(true);
          return res.json();
        })
        .then(data => setEvents(data))
        .catch(err => {
          console.error(err);
          setConnected(false);
        });
    }, 3000);

    return () => clearInterval(interval);
  }, []);

  const totalTargeted = events.reduce((sum, e) => sum + (e.amount_targeted_inr || 0), 0);
  const totalRecovered = events.reduce((sum, e) => sum + (e.amount_recovered_inr || 0), 0);
  const recoveryRate = totalTargeted > 0 ? (totalRecovered / totalTargeted) * 100 : 0;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-8">
      <div className="max-w-7xl mx-auto space-y-8">
        
        {/* Header */}
        <div className="flex justify-between items-center bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-gray-900">Remediate Intelligence</h1>
            <p className="text-gray-500 mt-1">Live Financial Remediation Dashboard</p>
          </div>
          <div className="flex items-center gap-3 bg-gray-50 px-4 py-2 rounded-lg border border-gray-100">
            <div className={`w-3 h-3 rounded-full ${connected ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
            <span className="text-sm font-medium text-gray-600">
              {connected ? 'Live Data Stream' : 'Disconnected'}
            </span>
          </div>
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
          <KpiCard title="Events Processed" value={events.length} icon={<Activity className="text-blue-500"/>} />
          <KpiCard title="Revenue Targeted" value={`₹${totalTargeted.toLocaleString('en-IN')}`} icon={<AlertTriangle className="text-orange-500"/>} />
          <KpiCard title="Revenue Recovered" value={`₹${totalRecovered.toLocaleString('en-IN')}`} icon={<IndianRupee className="text-emerald-500"/>} />
          <KpiCard title="Recovery Rate" value={`${recoveryRate.toFixed(1)}%`} icon={<CheckCircle className="text-indigo-500"/>} />
        </div>

        {/* Recent Events Table */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          <div className="px-6 py-5 border-b border-gray-100">
            <h2 className="text-lg font-semibold text-gray-900">Recent Interventions</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider">
                  <th className="px-6 py-4 font-semibold">Event ID</th>
                  <th className="px-6 py-4 font-semibold">Customer</th>
                  <th className="px-6 py-4 font-semibold">Amount</th>
                  <th className="px-6 py-4 font-semibold">Tool Used</th>
                  <th className="px-6 py-4 font-semibold">Recovered</th>
                  <th className="px-6 py-4 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {[...events].reverse().slice(0, 15).map((e, idx) => (
                  <tr key={idx} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 text-sm font-mono text-gray-500">{e.event_id?.split('-')[0]}</td>
                    <td className="px-6 py-4 text-sm font-medium text-gray-900">{e.customer?.name || "Unknown"}</td>
                    <td className="px-6 py-4 text-sm">₹{(e.payment?.amount_inr || e.amount_targeted_inr || 0).toLocaleString()}</td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
                        {e.decision?.tool_called || e.tool_called || "N/A"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm font-medium text-emerald-600">
                      {(e.outcome?.amount_recovered_inr || e.amount_recovered_inr) > 0 ? `₹${(e.outcome?.amount_recovered_inr || e.amount_recovered_inr).toLocaleString()}` : '-'}
                    </td>
                    <td className="px-6 py-4">
                      {(e.outcome?.success || e.success) ? 
                        <CheckCircle className="w-5 h-5 text-emerald-500" /> : 
                        <XCircle className="w-5 h-5 text-red-400" />
                      }
                    </td>
                  </tr>
                ))}
                {events.length === 0 && (
                  <tr>
                    <td colSpan="6" className="px-6 py-12 text-center text-gray-500">
                      No events processed yet. Start the batch runner!
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}

function KpiCard({ title, value, icon }) {
  return (
    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center gap-4">
      <div className="p-3 bg-gray-50 rounded-lg">{icon}</div>
      <div>
        <p className="text-sm font-medium text-gray-500">{title}</p>
        <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
      </div>
    </div>
  );
}
