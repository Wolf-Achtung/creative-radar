// Entscheidungs-Queue (21.08.2026) — Wolfs UX-Befund: die Vorschlags-
// Queue war ueberladen und der haeufigste Fall („Titel fehlt in der
// Titelliste") hatte keinen direkten Weg. Diese Tests nageln die drei
// Faelle und ihre jeweils EINE primaere Aktion fest.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { searchTitles } from '../format';
import { CandidateDecisionCard, extractTitleFromNote, titelAnzeigeName } from './CandidateDecisionCard';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const basisAsset = {
  id: 'a1',
  title_id: null,
  post_url: 'https://instagram.test/p/abc',
  channel_name: 'Lionsgate Horror',
  channel_market: 'US',
  detected_at: '2026-08-13T10:00:00Z',
  visible_views: 891068,
  visible_likes: 41263,
  caption: 'BEWARE BOIÚNA — only in cinemas.',
};

const kandidat = {
  id: 'c1',
  suggested_title: 'beware',
  confidence: 0.9,
  llm_note: "KI unsicher: Der Post bewirbt 'Beware Boiúna', einen Titel, der nicht in der Kandidatenliste enthalten ist.",
};

describe('extractTitleFromNote', () => {
  it('zieht den zitierten Filmtitel aus der KI-Notiz', () => {
    expect(extractTitleFromNote(kandidat.llm_note)).toBe('Beware Boiúna');
    expect(extractTitleFromNote('Der Post bewirbt „Lügen über meine Mutter“ direkt.')).toBe('Lügen über meine Mutter');
    // 22.08.: die Low-9-Variante mit ’-Schluss (Wolfs „adaptation"-Fall —
    // die Vorbefuellung fiel auf den verstuemmelten suggested_title zurueck).
    expect(extractTitleFromNote('Der Post bewirbt den Film ‚H wie Habicht’, der nicht enthalten ist.')).toBe('H wie Habicht');
    expect(extractTitleFromNote('Der Post bewirbt ‚H wie Habicht‘ direkt.')).toBe('H wie Habicht');
    expect(extractTitleFromNote('keine Zitate hier')).toBe('');
    expect(extractTitleFromNote(null)).toBe('');
  });
});

describe('searchTitles', () => {
  const katalog = [
    { id: 't1', title_original: 'Lügen über meine Mutter' },
    { id: 't2', title_original: 'Beware Boiúna', aliases: ['boiuna'] },
    { id: 't3', title_original: 'Daniel' },
  ];

  it('findet Umlaut-Titel auch ohne Umlaute in der Eingabe — Wolfs Fall', () => {
    expect(searchTitles(katalog, 'lugen uber').map((t) => t.id)).toEqual(['t1']);
    expect(searchTitles(katalog, 'Lügen').map((t) => t.id)).toEqual(['t1']);
  });

  it('findet ueber Aliases und akzentuierte Originale', () => {
    expect(searchTitles(katalog, 'boiuna').map((t) => t.id)).toEqual(['t2']);
  });

  it('sucht erst ab zwei Zeichen', () => {
    expect(searchTitles(katalog, 'l')).toEqual([]);
  });
});

describe('titelAnzeigeName', () => {
  // 31.08.2026 — Wolfs Queue-Befund: der Zuordnen-Button zeigte nur den
  // Originaltitel (z. B. 트리거), obwohl der Match ueber den Lokaltitel
  // („Trigger") zustande kam. Ohne den Lokaltitel wirkt der Vorschlag
  // wie ein Fehlgriff und wird von Hand weggeklickt.
  it('stellt den Lokaltitel voran, wenn er vom Original abweicht', () => {
    expect(titelAnzeigeName({ title_original: '트리거', title_local: 'Trigger' })).toBe('Trigger (트리거)');
  });

  it('zeigt nur den Originaltitel, wenn Lokaltitel fehlt oder gleich ist', () => {
    expect(titelAnzeigeName({ title_original: 'Daniel' })).toBe('Daniel');
    expect(titelAnzeigeName({ title_original: 'Daniel', title_local: '  daniel ' })).toBe('Daniel');
    expect(titelAnzeigeName({ title_original: 'Daniel', title_local: '' })).toBe('Daniel');
    expect(titelAnzeigeName(null)).toBe('');
  });
});

describe('CandidateDecisionCard', () => {
  it('exakter Treffer mit abweichendem Lokaltitel: der Button zeigt beide Namen', () => {
    const onConfirm = vi.fn();
    const titel = { id: 't-kr', title_original: '트리거', title_local: 'Trigger' };
    render(
      <CandidateDecisionCard
        asset={basisAsset}
        titles={[titel]}
        busy={false}
        openCandidate={{ ...kandidat, suggested_title: 'trigger', llm_note: null }}
        candidateMatchedTitle={titel}
        onConfirmCandidate={onConfirm}
      />
    );

    // Der Name taucht doppelt auf (primaerer Button + Live-Suche-Treffer,
    // weil „trigger" auch ueber den Lokaltitel matcht) — beide zeigen beide Namen.
    const buttons = screen.getAllByRole('button', { name: '„Trigger (트리거)“ zuordnen' });
    fireEvent.click(buttons[0]);
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }), expect.objectContaining({ id: 'c1' }), titel);
  });

  it('Live-Suche: Tippen zeigt Katalog-Treffer, Klick ordnet zu statt anzulegen', () => {
    const onConfirm = vi.fn();
    const onCreate = vi.fn();
    const titel = { id: 't1', title_original: 'Lügen über meine Mutter' };
    render(
      <CandidateDecisionCard
        asset={basisAsset}
        titles={[titel]}
        busy={false}
        openCandidate={{ ...kandidat, llm_note: null }}
        candidateMatchedTitle={null}
        onConfirmCandidate={onConfirm}
        onCreateTitleFromCandidate={onCreate}
      />
    );

    fireEvent.change(screen.getByLabelText('Titelname für Suche oder Anlage'), {
      target: { value: 'lugen uber' },
    });
    fireEvent.click(screen.getByRole('button', { name: '„Lügen über meine Mutter“ zuordnen' }));
    expect(onConfirm).toHaveBeenCalledWith(basisAsset, expect.objectContaining({ id: 'c1' }), titel);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('bereits zugeordnet: auch dort laesst sich ein fehlender Titel anlegen', () => {
    const onCreate = vi.fn();
    render(
      <CandidateDecisionCard
        asset={{ ...basisAsset, title_id: 't-alt', title_name: 'Daniel' }}
        titles={[{ id: 't-alt', title_original: 'Daniel' }]}
        busy={false}
        openCandidate={kandidat}
        candidateMatchedTitle={null}
        onCreateTitleFromCandidate={onCreate}
      />
    );

    const eingabe = screen.getByLabelText('Titelname für Suche oder Anlage');
    expect(eingabe.value).toBe('Beware Boiúna');
    fireEvent.click(screen.getByRole('button', { name: 'Titel neu anlegen + stattdessen zuordnen' }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }), kandidat, 'Beware Boiúna');
  });

  it('Titel fehlt: Eingabe ist mit dem Titel aus der KI-Notiz vorbefuellt und legt an', () => {
    const onCreate = vi.fn();
    render(
      <CandidateDecisionCard
        asset={basisAsset}
        titles={[]}
        busy={false}
        openCandidate={kandidat}
        candidateMatchedTitle={null}
        onCreateTitleFromCandidate={onCreate}
      />
    );

    const eingabe = screen.getByLabelText('Titelname für Suche oder Anlage');
    expect(eingabe.value).toBe('Beware Boiúna');
    fireEvent.click(screen.getByRole('button', { name: 'Titel anlegen + zuordnen' }));
    expect(onCreate).toHaveBeenCalledWith(basisAsset, kandidat, 'Beware Boiúna');
  });

  it('exakter Treffer: die primaere Aktion ordnet mit einem Klick zu', () => {
    const onConfirm = vi.fn();
    const titel = { id: 't1', title_original: 'Bluey: The Movie' };
    render(
      <CandidateDecisionCard
        asset={basisAsset}
        titles={[titel]}
        busy={false}
        openCandidate={{ ...kandidat, suggested_title: 'the bluey movie', llm_note: null }}
        candidateMatchedTitle={titel}
        onConfirmCandidate={onConfirm}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: '„Bluey: The Movie“ zuordnen' }));
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }), expect.objectContaining({ id: 'c1' }), titel);
  });

  it('bereits zugeordnet: behalten ist primaer, Umzug braucht zwei Klicks', () => {
    const onConfirm = vi.fn();
    const onDismiss = vi.fn();
    const alt = { id: 't-alt', title_original: 'Daniel' };
    const neu = { id: 't-neu', title_original: 'Bluey: The Movie' };
    render(
      <CandidateDecisionCard
        asset={{ ...basisAsset, title_id: 't-alt', title_name: 'Daniel' }}
        titles={[alt, neu]}
        busy={false}
        openCandidate={{ ...kandidat, llm_note: null }}
        candidateMatchedTitle={neu}
        onConfirmCandidate={onConfirm}
        onDismissCandidate={onDismiss}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Stattdessen „Bluey: The Movie“ zuordnen/ }));
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Wirklich zu „Bluey: The Movie“ umziehen/ }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Zuordnung behalten — Vorschlag schließen' }));
    expect(onDismiss).toHaveBeenCalledWith(expect.objectContaining({ id: 'c1' }));
  });

  it('die Bildflaeche verlinkt immer auf den Original-Post', () => {
    render(
      <CandidateDecisionCard
        asset={basisAsset}
        titles={[]}
        busy={false}
        openCandidate={kandidat}
      />
    );

    const link = screen.getByTitle('Original-Post öffnen');
    expect(link.getAttribute('href')).toBe('https://instagram.test/p/abc');
  });

  it('das Vorschaubild kommt vom /api/thumbnails-Proxy, nicht aus den rohen Feldern', () => {
    render(
      <CandidateDecisionCard
        asset={{ ...basisAsset, visual_evidence_url: 'evidence/a1_x.jpg', thumbnail_url: 'https://scontent.cdninstagram.com/x.jpg' }}
        titles={[]}
        busy={false}
        openCandidate={kandidat}
      />
    );

    const bild = screen.getByAltText('Creative Vorschau');
    expect(bild.getAttribute('src')).toContain('/api/thumbnails/a1');
    expect(bild.getAttribute('src')).not.toContain('evidence/'), (
      'Der nackte Storage-Key ging als relative URL kaputt — die Karte '
      + 'muss den Proxy nutzen, der gespeicherte Evidence aufloest '
      + '(Wolfs kaputte Vorschaubilder vom 21.08.).'
    );
  });
});
