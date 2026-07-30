import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { Layout } from './components/Layout';
import { Navigation } from './components/Navigation';
import { Home } from './pages/Home';
import { Player } from './pages/Player';
import { Library } from './pages/Library';
import { Login } from './pages/Login';
import StressBall from './components/StressBall';
import { InProgressProvider } from './context/InProgressContext';
import { AuthProvider } from './context/AuthContext';

export default function App() {
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('theme') || 'dark';
  });

  useEffect(() => {
    document.body.className = theme;
    localStorage.setItem('theme', theme);
  }, [theme]);

  return (
    <AuthProvider>
      <InProgressProvider>
        <Router>
          <Layout>
            <Navigation theme={theme} setTheme={setTheme} />
            <main className="flex-grow flex flex-col z-10 w-full relative">
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/player" element={<Player />} />
                <Route path="/library" element={<Library />} />
                <Route path="/login" element={<Login />} />
              </Routes>
            </main>
            <StressBall />
          </Layout>
        </Router>
      </InProgressProvider>
    </AuthProvider>
  );
}
