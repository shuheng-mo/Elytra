import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';

export function useDataSources() {
  const [datasources, setDatasources] = useState([]);
  const [defaultSource, setDefaultSource] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getDataSources();
      setDatasources(data.datasources || []);
      setDefaultSource(data.default || null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { datasources, defaultSource, loading, error, reload: load };
}
