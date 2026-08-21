// Entscheidungs-Queue (21.08.2026) — Wolfs UX-Befund: die Vorschlags-
// Queue war ueberladen und der haeufigste Fall („Titel fehlt in der
// Titelliste") hatte keinen direkten Weg. Diese Tests nageln die drei
// Faelle und ihre jeweils EINE primaere Aktion fest.
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CandidateDecisionCard, extractTitleFromNote } from './CandidateDecisionCard';

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
    expect(extractTitleFromNote('keine Zitate hier')).toBe('');
    expect(extractTitleFromNote(null)).toBe('');
  });
});

describe('CandidateDecisionCard', () => {
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

    const eingabe = screen.getByLabelText('Titelname für die Anlage');
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
});
