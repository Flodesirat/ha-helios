#!/usr/bin/env python3
"""
Point d'entrée unique pour lancer la simulation Helios en développement.

Usage :
    python sim.py [options]

Délègue entièrement à custom_components/helios/simulation/run.py.
"""
import sys
import os

# Ajoute la racine du dépôt au path pour que les imports du composant fonctionnent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from custom_components.helios.simulation.run import main

if __name__ == "__main__":
    main()
