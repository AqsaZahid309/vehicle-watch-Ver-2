import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import { LiveEventsProvider } from "./hooks/useLiveEvents";
import AuthPage from "./pages/AuthPage";

const Overview = lazy(() => import("./pages/Overview"));
const LiveMap = lazy(() => import("./pages/LiveMap"));
const Vehicles = lazy(() => import("./pages/Vehicles"));
const VehicleDetail = lazy(() => import("./pages/VehicleDetail"));
const Alerts = lazy(() => import("./pages/Alerts"));
const Maintenance = lazy(() => import("./pages/Maintenance"));
const Trips = lazy(() => import("./pages/Trips"));
const Geofences = lazy(() => import("./pages/Geofences"));
const FuelPage = lazy(() => import("./pages/Fuel"));
const Reports = lazy(() => import("./pages/Reports"));
const Models = lazy(() => import("./pages/Models"));
const SettingsPage = lazy(() => import("./pages/Settings"));

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<AuthPage />} />
      </Routes>
    );
  }
  return (
    <LiveEventsProvider enabled>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="map" element={<LiveMap />} />
            <Route path="vehicles" element={<Vehicles />} />
            <Route path="vehicles/:id" element={<VehicleDetail />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="maintenance" element={<Maintenance />} />
            <Route path="trips" element={<Trips />} />
            <Route path="geofences" element={<Geofences />} />
            <Route path="fuel" element={<FuelPage />} />
            <Route path="reports" element={<Reports />} />
            <Route path="models" element={<Models />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </LiveEventsProvider>
  );
}
